# OAuth: Logging In with Your ChatGPT Subscription

> Before the tutorial, the plumbing.

Every step in this tutorial (00 through 17) makes HTTP calls to `chatgpt.com/backend-api/codex/responses`. Every one of those calls needs two things:

1. A valid **access token** proving you're a ChatGPT Plus/Pro subscriber.
2. Your **account id**, identifying which subscription absorbs the cost.

Those two values live in a file on your machine called the **Token_Store**. This walkthrough builds the mental model, then lets you poke at the Token_Store with your own hands before you write any agent code.

## Prerequisites

- A **ChatGPT Plus or Pro subscription**. API keys do not work here.
- `uv` installed ([docs](https://docs.astral.sh/uv/getting-started/installation/)).
- `curl` and `jq` on your PATH (standard on macOS/Linux).
- Python 3.11+.

## What you'll do in this walkthrough

1. Run `my-bot login` and watch a browser OAuth flow complete.
2. Inspect your Token_Store — the file on your disk that now holds credentials.
3. Force a token refresh without waiting for natural expiry.
4. Send a raw `curl` request to the Responses API, bypassing our Python layer entirely.
5. Send a raw request that triggers a tool call, so you see the SSE event wire format.
6. Run a small test suite that validates the invariants above.

By the end, you'll know what PKCE is, what an SSE event looks like on the wire, what `store: false` means, and why your Token_Store has six fields instead of one. Then the tutorial proper makes much more sense.

---

## Part 1 — Why OAuth at all?

### The problem with API keys

The obvious way to call an LLM is: you sign up on the provider's website, you get an API key, you paste the key into a config file, your code sends the key in a request header. Works fine for a toy.

Problems as you scale out of toy-land:

- **The key is a long-lived secret.** If it leaks, anyone who has it can spend your money until you manually rotate it.
- **The key has no scope or session.** Every request with that key is a first-class "you." There's no concept of "this token is for this machine, expires in an hour, can only do these things."
- **The key is attached to a billing plan.** If you already pay for ChatGPT Plus ($20/month), your API calls are billed *separately* on a per-token meter. You pay twice.

ChatGPT subscriptions expose a different path: the same OAuth flow the OpenAI Codex CLI uses. You log in once with your browser, you get a pair of tokens (an access token and a refresh token), you use the access token until it expires, and when it expires you trade the refresh token for a fresh access token. Your Plus/Pro subscription covers the calls — no extra bill.

The tradeoff: more mechanism than an API key. Login involves a browser. Refresh runs periodically. Tokens need somewhere to live on disk. This walkthrough explains all of it.

### The choice this tutorial made

This tutorial ships **only** the OAuth path. API-key support was removed entirely. Why?

- **Teaching clarity.** Two code paths is harder to follow than one.
- **The pain you feel when you screw up.** With OAuth, a typo in your config gets rejected with "don't put credentials here, run `my-bot login` instead." That error message is itself teaching — it reinforces the mental model.
- **One subscription, zero marginal cost.** If you have ChatGPT Plus, you're done. No new account, no billing surface, no extra secrets to juggle.

---

## Part 2 — PKCE (Proof Key for Code Exchange)

### The idea in one paragraph

Plain OAuth has a problem. The authorization server sends an **authorization code** back to your application through a browser redirect. Your application then trades that code for tokens at the token endpoint. The code is in flight through your browser, which makes it possible for a local attacker to intercept it.

PKCE closes this hole. Before sending the user to the browser, your application invents a random secret called the **code verifier**, hashes it with SHA-256 to get the **code challenge**, and sends only the hash out. When your application later trades the code for tokens, it sends the plaintext verifier. The server checks that `SHA256(verifier) == challenge`. If an attacker stole the code mid-flight, they can't redeem it without the verifier, which never left your application's memory.

### What PKCE looks like in `oauth.py`

```python
code_verifier = _b64url(secrets.token_bytes(64))
code_challenge = _b64url(hashlib.sha256(code_verifier.encode()).digest())
```

- `secrets.token_bytes(64)` gives 64 random bytes.
- We base64-url-encode them to get the verifier (a string ChatGPT's server can handle in a URL).
- We SHA-256 that string and base64-url-encode the digest to get the challenge.

The verifier stays in the program's memory. The challenge goes out in the authorize URL:

```
https://auth.openai.com/oauth/authorize?
  response_type=code
  &client_id=app_EMoamEEZ73f0CkXaXp7hrann
  &redirect_uri=http://localhost:1455/auth/callback
  &scope=openid profile email offline_access api.connectors.read api.connectors.invoke
  &code_challenge=<challenge>
  &code_challenge_method=S256
  &state=<random>
```

You click "Allow." The browser redirects to `http://localhost:1455/auth/callback?code=...&state=...`. Our code matches the `state` (prevents cross-site request forgery), then POSTs to `https://auth.openai.com/oauth/token`:

```
grant_type=authorization_code
code=<authorization_code>
client_id=app_EMoamEEZ73f0CkXaXp7hrann
redirect_uri=http://localhost:1455/auth/callback
code_verifier=<the_verifier_we_kept_in_memory>
```

The server verifies that `SHA256(code_verifier) == challenge_it_remembers_from_earlier`. If yes, it returns tokens. If no, it refuses.

### The loopback trick

Our app isn't a web server. It's a CLI tool. So how does the redirect work?

We briefly become a server. Right before we open the browser, we bind a tiny HTTP listener on `127.0.0.1:1455`. When the browser redirects, our listener catches the `GET /auth/callback?code=...&state=...` request, extracts the code, and shuts down. The whole listener lives for seconds.

Why port 1455 specifically? Because the ChatGPT OAuth client registration (the `app_EMoamEEZ73...` id) has `http://localhost:1455/auth/callback` hardcoded as an allowed redirect. If you try a different port, the authorization server will reject the request. We don't get to pick.

This is why `ChatGPTOAuth.login()` fails immediately if port 1455 is already in use. No graceful fallback exists.

---

## Part 3 — Do it: `my-bot login`

Time to actually log in. From the repo root:

```bash
cd 000-oauth
uv sync
uv run my-bot login
```

What you should see:

1. Your default browser opens to `https://auth.openai.com/oauth/authorize?...`.
2. You sign in (or confirm if already signed in).
3. You click "Allow" to grant the requested scopes.
4. The browser redirects to `localhost:1455/auth/callback?...` and shows "Login complete. You may close this tab."
5. Your terminal prints:
   ```
   Logged in as acct_<your-id>
   Token store: /Users/you/.config/mybot/chatgpt_oauth.json
   ```

### If something goes wrong

| Symptom | What happened | Fix |
|---|---|---|
| "Could not bind the ChatGPT login callback server on 127.0.0.1:1455" | Port 1455 is already taken by another `my-bot login` in progress, or some other process. | Close the other process. Check with `lsof -i :1455`. Retry. |
| "OAuth state mismatch" | The redirect came back with the wrong `state` value. Almost always a retry issue (maybe an old browser tab fired). | Run `my-bot login` again. |
| "ChatGPT authorize server returned error" | The authorize server refused (e.g. you clicked "Deny"). | Retry and click "Allow." |
| Browser doesn't open | Firewall / headless machine. | Copy the URL from the terminal output and paste it into a browser. |

---

## Part 4 — The Token_Store

Once login succeeds, we have four things:

- **`access_token`** — a short-lived JWT (usually ~1 hour) sent in `Authorization: Bearer ...` headers.
- **`refresh_token`** — a long-lived credential used to trade for new access tokens.
- **`expires_at`** — the UTC timestamp after which the `access_token` is dead.
- **`account_id`** — extracted from the `id_token` JWT's namespaced `chatgpt_account_id` claim. Sent in the `ChatGPT-Account-Id` header on every Responses API call.

These live in the **Token_Store**:

- **POSIX:** `~/.config/mybot/chatgpt_oauth.json` (or `$XDG_CONFIG_HOME/mybot/...` if set).
- **Windows:** `%APPDATA%\mybot\chatgpt_oauth.json`.

### Why outside the repo tree?

Two reasons:

1. **Accidental commits.** If it lived inside the repo, a careless `git add .` would commit your credentials.
2. **Machine-level resource.** You log in once per machine. All 18 steps read the same Token_Store. Putting it inside a specific step would bind it to one step's lifecycle.

### POSIX mode 0600

On POSIX, we `chmod 0600` the file immediately after writing. That means "only the owning user can read or write; no group, no other users." On a multi-user box, another user tries `cat ~/.config/mybot/chatgpt_oauth.json` and gets "permission denied." Modest protection, but real.

(On Windows, file modes don't work the same way. We don't fail if `chmod` isn't supported.)

### Atomic writes

We never overwrite the file in place. The pattern is:

1. Write to a temp file in the same directory (e.g. `.chatgpt_oauth.abc123.tmp`).
2. `chmod 0600` the temp file.
3. `os.replace(temp, target)` — an atomic rename on all supported filesystems.

If anything fails between step 1 and step 3 (process crash, disk full), the old Token_Store remains untouched. You can always recover from a failed write by just running `my-bot login` again.

### What's in the id_token

`account_id` comes from a namespaced claim inside the `id_token` JWT:

```json
{
  "https://api.openai.com/auth": {
    "chatgpt_account_id": "acct_..."
  }
}
```

The `id_token` is JWT-signed but we don't verify the signature. We just base64-decode the payload to grab the claim. Why no verification? Because we received the token over HTTPS directly from `auth.openai.com`. There's no man-in-the-middle scenario that a signature check would catch here. The token is informational.

---

## Part 5 — Do it: inspect your Token_Store

Now that you've logged in, the Token_Store is on your disk. Peek at it safely:

```bash
./scripts/inspect_token_store.sh
```

This prints:
- The file path.
- Which fields are present (without exposing secret values).
- The POSIX mode.
- The absolute expiry timestamp and "valid for" delta.

You should see all five fields present, mode `-rw-------` (`0600`), and an `expires_at` roughly an hour in the future.

If you want to see the raw JSON (mild privacy risk — it contains your real tokens):

```bash
cat ~/.config/mybot/chatgpt_oauth.json | jq
```

Don't share the output, don't paste it in chat, don't commit it.

---

## Part 6 — The refresh loop

### The 60-second safety margin

`access_token` expires in ~1 hour. We don't wait until expiry to refresh, because that would mean a user's request might fail on the boundary. Instead:

```python
def needs_refresh(self, now=None) -> bool:
    now = now or datetime.now(timezone.utc)
    return self.expires_at <= now + timedelta(seconds=60)
```

If the token expires in 60 seconds or less, treat it as already expired and refresh before using it.

Why 60 seconds specifically?

- A Responses API call can stream for 30+ seconds.
- Our margin (60 s) is bigger than our worst-case request duration.
- So if `needs_refresh()` says "no" when we start a request, the token will still be valid when that request finishes.

### The refresh POST

```http
POST https://auth.openai.com/oauth/token
Content-Type: application/x-www-form-urlencoded

grant_type=refresh_token
refresh_token=<the_old_refresh_token>
client_id=app_EMoamEEZ73f0CkXaXp7hrann
```

Three outcomes:

1. **2xx:** We get a new `access_token`, a new `expires_in`, and sometimes a new `refresh_token`. We atomically write the new credentials. We return the new `access_token` to whoever called `access_token()`.

2. **400 or 401 (`invalid_grant`):** The refresh_token was rejected. The user needs to re-authenticate. We raise a clear error with "run `my-bot login` again" in the message. We do NOT overwrite the Token_Store — preserving the old one means the user's existing state is stable while they decide what to do.

3. **5xx or a network error:** The refresh *might* have worked on the server, or it might not. Safe behavior: surface the error to the caller and leave the Token_Store alone. Next time `access_token()` is called, we'll try again with the same refresh_token.

### The `refresh_token` can rotate

OAuth spec says the server MAY (but doesn't have to) return a new `refresh_token` in the refresh response. ChatGPT's server sometimes does. Our code handles both:

```python
new_creds = OAuthCredentials(
    ...
    refresh_token=payload.get("refresh_token") or creds.refresh_token,
    ...
)
```

"Use the new one if the server sent it; otherwise keep the old one." This rule matters: if we overwrote with an empty string when the server omitted it, we'd break future refreshes.

### `asyncio.Lock` serializes reads

Consider two `chat()` calls in an async program happening at the same time. Both hit `access_token()` simultaneously. Without coordination:

1. Both read the old credentials.
2. Both notice they're within the refresh margin.
3. Both POST to the token endpoint.
4. First POST succeeds, gets new tokens.
5. Second POST tries to use the (now rotated) refresh_token — rejected as `invalid_grant`.
6. The user gets a spurious "run `my-bot login` again" error.

The fix: one `asyncio.Lock` on the `ChatGPTOAuth` instance. Every call to `access_token()` acquires it, runs the read-refresh-write sequence, releases it. Serial execution. No race.

This works for one process. For multiple processes sharing the same Token_Store, you'd need an OS-level file lock (`fcntl.flock` on POSIX, `msvcrt.locking` on Windows). Step 16 of the tutorial discusses that as future work; we haven't implemented it.

---

## Part 7 — Do it: force a refresh

You don't want to wait an hour for the access token to expire naturally. Simulate it:

```bash
./scripts/force_refresh.sh
```

What it does:

1. Backs up your Token_Store to `chatgpt_oauth.json.bak`.
2. Rewrites `expires_at` to 30 seconds in the future.
3. Runs `my-bot chat` in step 00 with a one-shot message.
4. Shows the `expires_at` before and after — confirming that the refresh pushed it forward.
5. Prints a success message if the refresh worked.

Run it. Compare the `expires_at` before and after. The "before" should be 30 seconds after run time; the "after" should be about an hour after run time. That's the refresh loop in action.

---

## Part 8 — SSE (Server-Sent Events)

### What SSE is

The Responses API doesn't reply with a single JSON blob. It replies with a **stream** of events, one at a time, separated by blank lines. The wire format is called Server-Sent Events (SSE).

A raw SSE response to a chat message looks like this:

```
event: response.output_text.delta
data: {"delta":"Hel"}

event: response.output_text.delta
data: {"delta":"lo"}

event: response.output_text.delta
data: {"delta":"!"}

event: response.output_text.done
data: {"text":"Hello!"}

```

Each `event:` line names the event type. Each `data:` line carries a JSON payload. A blank line (`\n\n`) ends the event. The stream stays open until the server closes the connection.

This lets you, in principle, display the reply token-by-token as the model generates it (a "typing" effect). This tutorial doesn't do that — we accumulate the whole response and return it as one string — but the transport is streaming underneath.

### Why streaming is mandatory

You don't get to ask the Responses API for a non-streaming response. Setting `stream: false` in the request body is rejected. This is one of the three backend invariants we enforce:

```python
REQUIRED_STREAM = True
REQUIRED_STORE = False
```

(The other one, `store: false`, means "don't save this response in OpenAI's conversation history store" — required for the subscription endpoint.)

### How we parse SSE

`httpx.AsyncClient.stream("POST", ...)` exposes an `aiter_lines()` method on the response object. We walk the lines, accumulate an event buffer, and emit on the blank-line boundary:

```python
async def _iter_sse(resp: httpx.Response) -> AsyncIterator[SSEEvent]:
    event_type: str | None = None
    data_buf: list[str] = []
    async for line in resp.aiter_lines():
        if line == "":
            if event_type is not None and data_buf:
                raw = "\n".join(data_buf)
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    data = {}
                yield SSEEvent(type=event_type, data=data)
            event_type = None
            data_buf = []
            continue
        if line.startswith(":"):
            continue  # SSE comment
        if line.startswith("event:"):
            event_type = line[len("event:"):].strip()
        elif line.startswith("data:"):
            data_buf.append(line[len("data:"):].lstrip())
```

Concepts to notice:

- `async for` over `aiter_lines()` — consume a streaming HTTP response without blocking the event loop.
- Blank line (`""`) means "the event is complete; emit it."
- A line starting with `:` is an SSE comment (heartbeat / keepalive from the server). We skip it.
- If a `data:` payload doesn't parse as JSON, we emit the event with an empty dict rather than crash. Bad events shouldn't kill a whole conversation.

### The events we actually care about

The Responses API emits many event types. Ours only looks at six:

| Event | What it means |
|---|---|
| `response.output_text.delta` | Another chunk of the assistant's text response. `data.delta` is the new chunk. |
| `response.output_text.done` | The full text response is finalized. `data.text` is the authoritative final value. |
| `response.output_item.added` | A new output item started. If the item's `type` is `function_call`, we note its name + call_id. |
| `response.function_call_arguments.delta` | Another chunk of a function_call's JSON arguments. |
| `response.function_call_arguments.done` | The full arguments JSON is finalized. |
| `response.output_item.done` | An output item (text or function_call) is complete. |

Everything else is silently ignored. Forward-compat: the backend can add new event types without breaking us.

### A useful invariant

Concatenating all `response.output_text.delta` payloads equals the `response.output_text.done.text` value. In code:

```python
"".join(deltas) == final_text
```

This is Property 8 in our Hypothesis test suite. Why care? Because it's the same invariant a streaming UI relies on: if you rendered the deltas as they came in, the result you display is the same as the final authoritative string. No post-hoc correction needed.

### Key discovery during live testing

When we first built the SSE parser, we assumed the event names would be things like `response.function_call.arguments.delta` (with a dot between `function_call` and `arguments`). Turned out the real backend emits `response.function_call_arguments.delta` (underscore, no dot), plus a wrapping `response.output_item.done` to mark completion. We only found out when we ran an end-to-end test against a real ChatGPT account and saw the model's tool call fail silently.

The lesson: event names are wire-contract. They're not in any public spec document we're aware of. Our parser now handles both the real names AND the original guesses as forward-compat, and if the backend changes them again we'll find out during live verification.

---

## Part 9 — Do it: touch the wire with `curl`

The real way to build confidence in your SSE understanding is to generate an SSE stream yourself, bypassing our Python layer entirely.

### Plain chat

```bash
./scripts/probe_chat.sh
```

What it does: reads your access_token and account_id from the Token_Store, then POSTs a minimal request to `https://chatgpt.com/backend-api/codex/responses` with `curl -N` (no buffering). You see the raw SSE events stream past your terminal.

Look for:
- A sequence of `response.output_text.delta` events, each with a partial string in `data.delta`.
- A final `response.output_text.done` event containing the full text in `data.text`.
- HTTP 200 at the end.

### Tool call

```bash
./scripts/probe_tools.sh
```

Same thing, but with a `tools` array in the request body declaring a `bash` function. The model is instructed to call it. You see:
- `response.output_item.added` with `item.type == "function_call"` carrying the tool's name and call_id.
- A sequence of `response.function_call_arguments.delta` events building up the JSON arguments.
- A terminal `response.function_call_arguments.done` with the full arguments string.
- A wrapping `response.output_item.done` confirming the function_call is complete.

This is the exact event shape our Python code aggregates in `aggregate_stream()`. Now that you've seen it on the wire, the aggregator function makes sense.

---

## Part 10 — The Responses API request

### The URL

```
POST https://chatgpt.com/backend-api/codex/responses
```

This is NOT `api.openai.com`. It's a separate backend designed for the Codex CLI. Regular OpenAI API keys won't work here, and an API-key user's billing setup doesn't apply.

### The required headers

```
Authorization: Bearer <access_token>
ChatGPT-Account-Id: <account_id>
Content-Type: application/json
Accept: text/event-stream
originator: codex_cli_rs
```

- `Accept: text/event-stream` — "I expect SSE, don't try to give me plain JSON."
- `originator: codex_cli_rs` — a user-agent-like hint matching the one Codex CLI sends. We impersonate Codex CLI enough to be treated like one.
- `ChatGPT-Account-Id` — tells the server which ChatGPT account's subscription should absorb the cost of this request.

### The request body

```json
{
  "model": "gpt-5.4",
  "instructions": "You are a helpful assistant.",
  "input": [
    {"role": "user", "content": "What's 2+2?"}
  ],
  "stream": true,
  "store": false
}
```

This is NOT the Chat Completions format. Chat Completions uses a flat `messages` array with everything including the system prompt inside. The Responses API splits the system prompt out into `instructions` at the top level, and puts everything else into `input`.

Tool calls and tool results are also shaped differently. Where Chat Completions nests them on assistant messages, the Responses API puts them as separate `input` items:

- Tool call in history: `{"type": "function_call", "call_id": "...", "name": "...", "arguments": "..."}`
- Tool result in history: `{"type": "function_call_output", "call_id": "...", "output": "..."}`

This is why `_translate_messages` in `base.py` exists. The rest of the tutorial speaks Chat-Completions shape (simpler, more familiar). We translate at the boundary.

### Why `store: false`

If the subscription backend stored your prompts, they'd show up in your ChatGPT conversation history on chatgpt.com. That's almost never what you want when you're using the backend through code. `store: false` tells the server "this is API traffic, not chat traffic, don't save it."

### Why `stream: true`

Enforced by the backend, can't be turned off. See the SSE section.

---

## Part 11 — Do it: run the OAuth-only test suite

We've written a small test suite that validates only the OAuth layer — no agent, no session, no tools. Just login, refresh, Token_Store, SSE parsing.

```bash
cd 000-oauth
uv run pytest tests/ -v
```

All tests should pass. Each test's docstring explains which invariant it validates. If you want to learn the invariants through code rather than prose, read the test files:

- `tests/test_oauth_login_simulation.py` — end-to-end login flow, mocked.
- `tests/test_oauth_refresh.py` — when refresh fires, what happens on success/failure, Token_Store monotonicity.
- `tests/test_oauth_secrets.py` — confirms that even maliciously-crafted server responses can't leak your tokens into error text.
- `tests/test_sse_parser.py` — SSE framing invariants, delta concatenation, event aggregation.

---

## Part 12 — Putting it all together

Here's what happens end-to-end when you type a message in `my-bot chat`:

```
1. User types "hello"
2. AgentSession.chat("hello") is called.
3. It appends {"role": "user", "content": "hello"} to state.messages.
4. It calls llm.chat(state.build_messages()).
5. LLMProvider.chat() calls _resolve_credential().
6. _resolve_credential calls oauth.access_token() (acquires asyncio.Lock).
7. oauth checks Token_Store; if expires_at <= now + 60s, calls _refresh().
8. If we got here with a valid token, the lock releases and we return it.
9. LLMProvider calls oauth.account_id() (same lock, separate call).
10. LLMProvider builds a ResponsesRequest.
    - _translate_messages splits the history into (instructions, input).
    - _translate_tools flattens tool schemas (if any).
11. ResponsesClient.stream() POSTs to chatgpt.com/backend-api/codex/responses.
12. HTTP response is streaming (Content-Type: text/event-stream).
13. _iter_sse parses each event off the wire as it arrives.
14. aggregate_stream accumulates text deltas, captures tool calls.
15. When the stream closes, aggregate_stream returns AggregatedResponse.
16. LLMProvider returns (content, tool_calls) to AgentSession.
17. AgentSession appends {"role": "assistant", "content": content} to history.
18. AgentSession returns content (or loops if there are tool calls).
19. ChatLoop prints the content.
20. User types again; go to step 2.
```

Nineteen steps to answer "hello." And that's the point. An agent isn't one thing; it's a stack of narrow responsibilities cooperating.

Once you understand this pipeline, the 18 tutorial steps make sense: each one adds a piece (tools, persistence, compaction, channels, etc.) without changing the underlying flow.

---

## What's Next

You're done with the OAuth walkthrough. Your Token_Store is populated, you've seen the wire protocol, you've poked at the refresh loop, and the invariants are green in your test suite.

**Next:** [`../00-chat-loop/`](../00-chat-loop/) — build the simplest possible agent that uses everything you just learned.

---

## Reference

### Source files in this step

- [`src/mybot/provider/llm/oauth.py`](src/mybot/provider/llm/oauth.py) — login + refresh + Token_Store.
- [`src/mybot/provider/llm/responses.py`](src/mybot/provider/llm/responses.py) — SSE client + aggregator.
- [`src/mybot/cli/main.py`](src/mybot/cli/main.py) — the `my-bot login` Typer command.

These same files are copied byte-identically into every subsequent step (`00-chat-loop` through `17-memory`), plus an `LLMProvider` layer on top.

### External

- [RFC 7636: PKCE](https://datatracker.ietf.org/doc/html/rfc7636) — the official spec.
- [OAuth 2.0 spec (RFC 6749)](https://datatracker.ietf.org/doc/html/rfc6749) — the broader auth flow this extends.
- [Server-Sent Events spec](https://html.spec.whatwg.org/multipage/server-sent-events.html) — wire format details.
- [OpenAI Codex CLI source](https://github.com/openai/codex) — where we learned the exact scopes, originator, and client_id.
