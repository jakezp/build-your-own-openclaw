"""Shared ChatGPT OAuth module: PKCE login, token store, and refresh logic.

This module is the single place the tutorial owns the ChatGPT subscription
OAuth flow. It is intentionally small and self-contained so a learner can
read it once in step 00 and skim-verify it in later steps (the file is
copied byte-identically across all 18 steps).

Constants are pinned to the values used by OpenAI Codex CLI. If Codex CLI
changes these upstream, update them here or the tutorial's login will break.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import http.server
import json
import os
import secrets
import tempfile
import threading
import urllib.parse
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import httpx
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Pinned constants (confirmed against openai/codex Rust source).
# ---------------------------------------------------------------------------

CHATGPT_API_BASE = "https://chatgpt.com/backend-api/codex"
CHATGPT_AUTHORIZE_URL = "https://auth.openai.com/oauth/authorize"
CHATGPT_TOKEN_URL = "https://auth.openai.com/oauth/token"
CHATGPT_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CHATGPT_SCOPES = (
    "openid profile email offline_access "
    "api.connectors.read api.connectors.invoke"
)
# Matches codex-rs/login/src/auth/default_client.rs::DEFAULT_ORIGINATOR.
CHATGPT_ORIGINATOR = "codex_cli_rs"
# The ChatGPT OAuth client is registered with loopback redirect URIs that
# use a fixed port, the `localhost` host literal, and the `/auth/callback`
# path. Ephemeral ports or `127.0.0.1` are rejected by the authorize server
# as `unknown_error`.
CHATGPT_LOGIN_PORT = 1455
CHATGPT_REDIRECT_PATH = "/auth/callback"

REFRESH_SAFETY_MARGIN_SECONDS = 60


# ---------------------------------------------------------------------------
# Data classes / models.
# ---------------------------------------------------------------------------


@dataclass
class LoginResult:
    """Result of a successful one-shot ChatGPT OAuth login."""

    token_store_path: Path
    account_id: Optional[str]


class OAuthCredentials(BaseModel):
    """Persisted OAuth credentials for a ChatGPT subscription."""

    access_token: str
    refresh_token: str
    expires_at: datetime
    account_id: Optional[str] = None
    id_token: Optional[str] = None

    @classmethod
    def from_token_response(cls, payload: dict) -> "OAuthCredentials":
        """Build credentials from a raw token-endpoint JSON response."""
        expires_in = int(payload["expires_in"])
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
        # Codex CLI exposes account_id only via the id_token JWT's namespaced
        # claim. Keep the payload.get("account_id") short-circuit as a
        # defensive no-op in case the backend ever starts returning it
        # directly.
        account_id = payload.get("account_id") or _account_id_from_id_token(
            payload.get("id_token")
        )
        return cls(
            access_token=payload["access_token"],
            refresh_token=payload["refresh_token"],
            expires_at=expires_at,
            account_id=account_id,
            id_token=payload.get("id_token"),
        )

    def needs_refresh(self, now: Optional[datetime] = None) -> bool:
        """True if the access token is within the refresh safety margin."""
        now = now or datetime.now(timezone.utc)
        return self.expires_at <= now + timedelta(
            seconds=REFRESH_SAFETY_MARGIN_SECONDS
        )


# ---------------------------------------------------------------------------
# Token store.
# ---------------------------------------------------------------------------


class TokenStore:
    """Thin wrapper around the JSON file that holds OAuthCredentials."""

    def __init__(self, path: Path):
        self.path = path

    @classmethod
    def default_path(cls) -> Path:
        return _default_path()

    @classmethod
    def default(cls) -> "TokenStore":
        return cls(cls.default_path())

    def exists(self) -> bool:
        return self.path.exists()

    def read(self) -> OAuthCredentials:
        with open(self.path, "r", encoding="utf-8") as f:
            return OAuthCredentials.model_validate_json(f.read())

    def write(self, creds: OAuthCredentials) -> None:
        """Atomically write credentials with POSIX mode 0600.

        Writes a temp file in the same directory, chmods it before the
        swap so the atomic rename hands off an already-restricted file,
        then replaces the target path. On any failure the temp file is
        cleaned up and the prior Token_Store is left intact.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # model_dump_json() serializes datetime as ISO-8601 with timezone
        # offset in pydantic v2, which is what we want on disk.
        data = creds.model_dump_json()
        fd, tmp = tempfile.mkstemp(
            prefix=".chatgpt_oauth.",
            suffix=".tmp",
            dir=str(self.path.parent),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(data)
            if os.name == "posix":
                try:
                    os.chmod(tmp, 0o600)
                except OSError:
                    # Non-fatal on filesystems that don't support chmod.
                    pass
            os.replace(tmp, self.path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _default_path() -> Path:
    """Resolve the default Token_Store path for the current platform."""
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else (Path.home() / ".mybot")
        return base / "mybot" / "chatgpt_oauth.json"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else (Path.home() / ".config")
    return base / "mybot" / "chatgpt_oauth.json"


def _b64url(data: bytes) -> str:
    """URL-safe base64 encode with padding stripped."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _account_id_from_id_token(id_token: Optional[str]) -> Optional[str]:
    """Decode the id_token JWT payload and read the chatgpt_account_id claim.

    Codex CLI stores the account identifier in the namespaced claim
    ``https://api.openai.com/auth.chatgpt_account_id`` inside the id_token
    JWT payload — NOT in the standard ``sub`` claim and NOT as a
    top-level field on the token response. Returns None on any decode or
    lookup failure.
    """
    if not id_token:
        return None
    try:
        parts = id_token.split(".")
        if len(parts) < 2:
            return None
        payload_seg = parts[1]
        # url-safe base64 requires padding up to a multiple of 4
        payload_seg = payload_seg + "=" * (-len(payload_seg) % 4)
        payload_bytes = base64.urlsafe_b64decode(payload_seg.encode("ascii"))
        claims = json.loads(payload_bytes.decode("utf-8"))
        ns = claims.get("https://api.openai.com/auth")
        if not isinstance(ns, dict):
            return None
        account_id = ns.get("chatgpt_account_id")
        if isinstance(account_id, str) and account_id:
            return account_id
        return None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# ChatGPTOAuth orchestrator: login + access_token (+ silent refresh).
# ---------------------------------------------------------------------------


class ChatGPTOAuth:
    """One-time PKCE login and runtime access-token resolution for ChatGPT."""

    def __init__(self, store: Optional[TokenStore] = None):
        self._store = store or TokenStore.default()
        self._lock = asyncio.Lock()

    # -- login --------------------------------------------------------------

    def login(self) -> LoginResult:
        """Run the one-shot PKCE S256 browser login and persist credentials."""
        code_verifier = _b64url(secrets.token_bytes(64))
        code_challenge = _b64url(
            hashlib.sha256(code_verifier.encode()).digest()
        )
        state = _b64url(secrets.token_bytes(32))

        received: dict[str, str] = {}
        done = threading.Event()

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self_) -> None:  # noqa: N802, N805
                parsed = urllib.parse.urlparse(self_.path)
                if parsed.path != CHATGPT_REDIRECT_PATH:
                    self_.send_response(404)
                    self_.end_headers()
                    return
                qs = urllib.parse.parse_qs(parsed.query)
                received["code"] = qs.get("code", [""])[0]
                received["state"] = qs.get("state", [""])[0]
                received["error"] = qs.get("error", [""])[0]
                received["error_description"] = qs.get(
                    "error_description", [""]
                )[0]
                self_.send_response(200)
                self_.send_header("Content-Type", "text/html")
                self_.end_headers()
                self_.wfile.write(
                    b"<html><body>Login complete. You may close this tab."
                    b"</body></html>"
                )
                done.set()

            def log_message(self_, *args, **kwargs) -> None:  # noqa: N805
                # Silence the default stdout request-log spew.
                return

        # The ChatGPT OAuth client is registered for a fixed loopback port.
        try:
            server = http.server.ThreadingHTTPServer(
                ("127.0.0.1", CHATGPT_LOGIN_PORT), Handler
            )
        except OSError as e:
            raise RuntimeError(
                f"Could not bind the ChatGPT login callback server on "
                f"127.0.0.1:{CHATGPT_LOGIN_PORT} ({e}). Another login may "
                f"already be in progress; stop it and retry."
            ) from e
        port = server.server_address[1]
        redirect_uri = f"http://localhost:{port}{CHATGPT_REDIRECT_PATH}"
        threading.Thread(target=server.serve_forever, daemon=True).start()

        try:
            authorize_url = CHATGPT_AUTHORIZE_URL + "?" + urllib.parse.urlencode(
                {
                    "response_type": "code",
                    "client_id": CHATGPT_CLIENT_ID,
                    "redirect_uri": redirect_uri,
                    "scope": CHATGPT_SCOPES,
                    "code_challenge": code_challenge,
                    "code_challenge_method": "S256",
                    "id_token_add_organizations": "true",
                    "codex_cli_simplified_flow": "true",
                    "state": state,
                    "originator": CHATGPT_ORIGINATOR,
                }
            )
            webbrowser.open(authorize_url)
            if not done.wait(timeout=300):
                raise RuntimeError("timed out waiting for browser redirect")
        finally:
            server.shutdown()

        if received.get("state") != state:
            raise RuntimeError("OAuth state mismatch; aborting login")
        if received.get("error"):
            # The authorize server surfaced an error (e.g., unknown_error,
            # access_denied). Surface the code and description without
            # echoing any PKCE or token material.
            err_code = received.get("error") or "unknown_error"
            err_desc = received.get("error_description") or ""
            raise RuntimeError(
                f"ChatGPT authorize server returned error: {err_code}"
                + (f" ({err_desc})" if err_desc else "")
            )
        code = received.get("code")
        if not code:
            raise RuntimeError(
                "OAuth redirect did not include an authorization code"
            )

        resp = httpx.post(
            CHATGPT_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": CHATGPT_CLIENT_ID,
                "redirect_uri": redirect_uri,
                "code_verifier": code_verifier,
            },
            timeout=30.0,
        )
        if resp.status_code // 100 != 2:
            # Truncate the body to bound any accidental token echo.
            raise RuntimeError(
                f"token exchange failed: HTTP {resp.status_code}: "
                f"{resp.text[:200]}"
            )

        payload = resp.json()
        creds = OAuthCredentials.from_token_response(payload)
        self._store.write(creds)
        return LoginResult(
            token_store_path=self._store.path,
            account_id=creds.account_id,
        )

    # -- runtime ------------------------------------------------------------

    async def access_token(self) -> str:
        """Return a fresh ChatGPT access token, refreshing if needed."""
        async with self._lock:
            if not self._store.exists():
                raise RuntimeError(
                    f"No ChatGPT OAuth token found at {self._store.path}. "
                    "Run `my-bot login` first."
                )
            try:
                creds = self._store.read()
            except Exception as e:
                raise RuntimeError(
                    f"Token store at {self._store.path} is unreadable: {e}. "
                    "Run `my-bot login` to re-create it."
                ) from e

            if not creds.needs_refresh():
                return creds.access_token

            refreshed = await self._refresh(creds)
            return refreshed.access_token

    async def account_id(self) -> str:
        """Return the chatgpt_account_id claim from the stored credentials.

        Raises a clear error if the Token_Store is missing or the stored
        account_id is empty. Acquires the same asyncio.Lock as
        access_token() so concurrent readers/writers don't race on the
        on-disk file.
        """
        async with self._lock:
            if not self._store.exists():
                raise RuntimeError(
                    f"No ChatGPT OAuth token found at {self._store.path}. "
                    "Run `my-bot login` first."
                )
            creds = self._store.read()
            if not creds.account_id:
                raise RuntimeError(
                    f"Token store at {self._store.path} has no account_id. "
                    "Run `my-bot login` again to re-populate it."
                )
            return creds.account_id

    async def _refresh(self, creds: OAuthCredentials) -> OAuthCredentials:
        """Exchange the refresh token for fresh credentials and persist."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                CHATGPT_TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": creds.refresh_token,
                    "client_id": CHATGPT_CLIENT_ID,
                },
            )

        if resp.status_code in (400, 401):
            # invalid_grant / invalid_request: refresh_token rejected.
            # Do NOT overwrite the token store.
            err = ""
            try:
                err = resp.json().get("error", "") or ""
            except Exception:
                err = ""
            raise RuntimeError(
                f"ChatGPT refresh token rejected "
                f"({err or f'HTTP {resp.status_code}'}). "
                "Run `my-bot login` to sign in again."
            )
        if resp.status_code // 100 != 2:
            # Transient (5xx, network) — surface and leave the store alone.
            resp.raise_for_status()

        payload = resp.json()
        now_utc = datetime.now(timezone.utc)
        new_creds = OAuthCredentials(
            access_token=payload["access_token"],
            # If the server omits refresh_token, keep the prior one.
            refresh_token=payload.get("refresh_token") or creds.refresh_token,
            expires_at=now_utc + timedelta(seconds=int(payload["expires_in"])),
            account_id=(
                payload.get("account_id")
                or _account_id_from_id_token(payload.get("id_token"))
                or creds.account_id
            ),
            id_token=payload.get("id_token") or creds.id_token,
        )
        self._store.write(new_creds)
        return new_creds
