# OAuth Deep Dive

Everything you need to understand how this tutorial talks to ChatGPT.

This doc exists because the tutorial takes a specific transport decision — no API keys, no `litellm`, direct HTTPS calls to `chatgpt.com/backend-api/codex/responses` — and that decision has consequences that ripple through every step. Rather than re-explain the same material in eighteen step READMEs, we put it here once, carefully.

You can read this end-to-end before starting the tutorial. Or skip it and come back when a step references it. Both work.

---

## Part 1 — Why OAuth at all?

### The problem with API keys

The obvious way to call an LLM is: you sign up on the provider's website, you get an API key, you paste the key into a config file, your code sends the key in a request header. Works fine for a toy.

Problems as you scale out of toy-land:

- **The key is a long-lived secret.** If it leaks, anyone who has it can spend your money until you manually rotate it.
- **The key has no scope or session.** Every request with that key is a first-class "you." There's no concept of "this token is for this machine, expires in an hour, can only do these things."
- **The key is attached to a billing plan.** If you already pay for ChatGPT Plus ($20/month), your API calls are billed *separately* on a per-token meter. You pay twice.

ChatGPT subscriptions expose a different path: they use the same OAuth flow the OpenAI Codex CLI uses. You log in once with your browser, you get a pair of tokens (an access token and a refresh token), you use the access token until it expires, and when it expires you trade the refresh token for a fresh access token. Your Plus/Pro subscription covers the calls — no extra bill.

The tradeoff: it's more mechanism than an API key. Login involves a browser. Refresh runs periodically. Tokens need somewhere to live on disk. That's what the rest of this doc explains.

### The choice we made

This tutorial ships **only** the OAuth path. We removed API-key support entirely. Why?

- **Teaching clarity.** Two code paths is harder to follow than one. When the tutorial says "here's how an LLM call works," we want one path, not "well, it depends on which mode you're in."
- **The pain you feel when you screw up.** With OAuth, a typo in your config gets rejected with "don't put credentials here, run `my-bot login` instead." That error message is itself pedagogy — it teaches the reader the right mental model.
- **One subscription, zero marginal cost.** If you're reading this tutorial and you have ChatGPT Plus, you're done. No new account, no billing surface, no extra secrets to juggle.

---

## Part 2 — PKCE (Proof Key for Code Exchange)

### What it is, in one paragraph

Plain OAuth has a problem: the authorization server sends an authorization code back to your application (via a browser redirect), and your application then trades that code for tokens. The code flies through your browser, which makes it possible for a local attacker to intercept it. PKCE closes this hole: before sending the user to the browser, your application invents a random secret (the *code verifier*), hashes it with SHA-256 (the *code challenge*), and sends only the hash to the authorization server. When the application later trades the code for tokens, it also sends the plaintext verifier. The server checks that `SHA256(verifier) == challenge`. If some attacker stole the code mid-flight, they can't redeem it without the verifier, which never left your application.

### What it looks like in `oauth.py`

```python
code_verifier = _b64url(secrets.token_bytes(64))
code_challenge = _b64url(hashlib.sha256(code_verifier.encode()).digest())
```

`secrets.token_bytes(64)` gives 64 random bytes. We base64-url-encode them to get the verifier (a string ChatGPT's server can handle in a URL). We SHA-256 that string and base64-url-encode the digest to get the challenge.

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

You click "Allow." The browser redirects to `http://localhost:1455/auth/callback?code=<authorization_code>&state=<random>`. Your application matches the state (prevents cross-site request forgery), then POSTs to `https://auth.openai.com/oauth/token`:

```
grant_type=authorization_code
code=<authorization_code>
client_id=app_EMoamEEZ73f0CkXaXp7hrann
redirect_uri=http://localhost:1455/auth/callback
code_verifier=<the_verifier_we_kept_in_memory>
```

The server verifies that `SHA256(code_verifier) == challenge_it_remembers_from_earlier`. If yes, it returns the tokens. If no, it refuses.

### The loopback trick

Our app isn't a server. It's a CLI tool. So how does the redirect work?

We briefly become a server. Right before we open the browser, we bind a tiny HTTP listener on `127.0.0.1:1455`. When the browser redirects, our listener catches the `GET /auth/callback?code=...&state=...` request, extracts the code, then shuts down. The whole listener lives for seconds.

Why port 1455 specifically? Because the ChatGPT OAuth client registration (the `app_EMoamEEZ73...` id) has `http://localhost:1455/auth/callback` hardcoded as an allowed redirect. If you try a different port, the authorization server will reject the request. We don't get to pick.

This is why `ChatGPTOAuth.login()` fails immediately if port 1455 is already in use. No graceful fallback exists.

---

## Part 3 — The Token Store

Once login succeeds, we have four things:

- `access_token` — a short-lived JWT (usually ~1 hour) you send in `Authorization: Bearer ...` headers.
- `refresh_token` — a long-lived credential used to get a new access_token.
- `expires_at` — the UTC timestamp after which the access_token is dead.
- `account_id` — extracted from the `id_token` JWT's namespaced `chatgpt_account_id` claim. Goes in the `ChatGPT-Account-Id` header on every Responses API call.

We write these to:

- **POSIX:** `~/.config/mybot/chatgpt_oauth.json` (or `$XDG_CONFIG_HOME/mybot/...` if set).
- **Windows:** `%APPDATA%\mybot\chatgpt_oauth.json`.

### Why outside the repo?

The Token_Store lives OUTSIDE any tutorial step directory and outside `default_workspace/`. Two reasons:

1. **Accidental commits.** If it lived inside the repo, a careless `git add .` would commit your credentials.
2. **Machine-level resource.** You log in once per machine. All 18 steps read the same Token_Store. Putting it inside a specific step would bind it to one step's lifecycle.

### POSIX mode 0600

On POSIX systems, we `chmod 0600` the file immediately after writing. That means "only the owning user can read or write; no one else, not even their group." If another user on the same machine tries `cat ~/.config/mybot/chatgpt_oauth.json` on a multi-user box, they get "permission denied." Modest protection, but real.

(On Windows, file modes don't work the same way. We don't fail if `chmod` isn't supported.)

### Atomic writes

We never overwrite the file in place. The pattern is:

1. Write to a temp file in the same directory (e.g. `.chatgpt_oauth.abc123.tmp`).
2. `chmod 0600` the temp file.
3. `os.replace(temp, target)` — an atomic rename on all supported filesystems.

If anything fails between step 1 and step 3 (process crash, disk full), the old Token_Store remains untouched. You can always recover from a failed write by just running `my-bot login` again.

### What's in the id_token

`account_id` comes from the `id_token`, which is a JWT with a namespaced claim:

```json
{
  "https://api.openai.com/auth": {
    "chatgpt_account_id": "acct_..."
  }
}
```

The `id_token` is JWT-signed but we don't verify the signature. We just base64-decode the payload to grab the claim. Why no verification? Because we received the token over HTTPS directly from `auth.openai.com`. There's no man-in-the-middle scenario that a signature check would catch here. The token is informational.

---

## Part 4 — The refresh loop

### The 60-second safety margin

`access_token` expires, usually about an hour after login. But we don't wait until it expires to refresh — that would mean a user's request might fail on the boundary. Instead:

```python
def needs_refresh(self, now=None) -> bool:
    now = now or datetime.now(timezone.utc)
    return self.expires_at <= now + timedelta(seconds=60)
```

If the token expires in 60 seconds or less, treat it as already expired and refresh before using it.

That 60 seconds is arbitrary but sized for reality:
- A Responses API call can take 30+ seconds of streaming.
- Our margin (60s) is bigger than our worst-case request duration.
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

2. **400 or 401 (`invalid_grant`):** The refresh_token was rejected. This means the user needs to re-authenticate. We raise a clear error with "run `my-bot login` again" in the message. We do NOT overwrite the Token_Store — preserving the old one means the user's existing state is stable while they decide what to do.

3. **5xx or a network error:** The refresh *might* have worked on the server, or it might not have. Safe behavior: surface the error to the caller and leave the Token_Store alone. Next time `access_token()` is called, we'll try again with the same refresh_token.

### The `refresh_token` can rotate

OAuth spec says the server MAY (but doesn't have to) return a new `refresh_token` in the refresh response. ChatGPT's server sometimes does. Our code handles both:

```python
new_creds = OAuthCredentials(
    ...
    refresh_token=payload.get("refresh_token") or creds.refresh_token,
    ...
)
```

"Use the new one if the server sent it; otherwise keep the old one." This rule is important: if we overwrote with an empty string when the server omitted it, we'd break future refreshes.

### `asyncio.Lock` serializes reads

Consider two `chat()` calls made concurrently in an async program. Both hit `access_token()` at the same time. Without coordination:

1. Both read the old credentials.
2. Both notice they're within the refresh margin.
3. Both POST to the token endpoint.
4. First POST succeeds, gets new tokens.
5. Second POST tries to use the (now rotated) refresh_token — rejected as `invalid_grant`.
6. Now the user gets a spurious "run `my-bot login` again" error.

The fix: a single `asyncio.Lock` on the `ChatGPTOAuth` instance. Every call to `access_token()` acquires it, runs the read-refresh-write sequence, releases it. Serial execution. No race.

This works for one process. For multiple processes sharing the same Token_Store, you'd need an OS-level file lock (`fcntl.flock` on POSIX, `msvcrt.locking` on Windows). Step 16 of the tutorial discusses that as future work; we haven't implemented it.

---

## Part 5 — SSE (Server-Sent Events)

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

This lets you, in principle, display the reply token-by-token as the model generates it (a "typing" effect). Our tutorial doesn't do that — we accumulate the whole response and return it as one string — but the transport is streaming underneath.

### Why streaming is mandatory

You don't get to ask the Responses API for a non-streaming response. Setting `stream: false` in the request body is rejected. This is one of the three backend invariants we enforce:

```python
REQUIRED_STREAM = True
REQUIRED_STORE = False
```

(The other one, `store: false`, means "don't save this response in OpenAI's conversation history store" — required for this subscription endpoint.)

### How we parse SSE

`httpx.AsyncClient.stream("POST", ...)` exposes an `aiter_lines()` method on the response object. We walk the lines, accumulate an event buffer, and emit it on the blank-line boundary:

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

- `async for` over `aiter_lines()` — this is how we consume a streaming HTTP response without blocking the event loop.
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

Everything else gets silently ignored. Forward-compat: the backend can add new event types without breaking us.

### The delta-concatenation property

There's a useful invariant: the concatenation of all `response.output_text.delta` payloads equals the `response.output_text.done.text` value. In code:

```python
"".join(deltas) == final_text
```

We test this with Hypothesis (Property 8 / P8 in the test suite). Why care? Because it's the same invariant a streaming UI relies on: if you rendered the deltas as they came in, the result you display is the same as the final authoritative string. No post-hoc correction needed.

### A key discovery during live testing

When we first built the SSE parser, we assumed the event names would be things like `response.function_call.arguments.delta` (with a dot between `function_call` and `arguments`). Turned out the real backend emits `response.function_call_arguments.delta` (underscore, no dot), plus a wrapping `response.output_item.done` to mark completion. We only found this when we ran an end-to-end test against a real ChatGPT account and saw the model's tool call fail silently.

The lesson: event names are wire-contract. They're not in any public spec document we're aware of. Our parser now handles both the real names AND the original guesses, as forward-compat, and if the backend changes them again we'll find out during live verification.

---

## Part 6 — The Responses API request

### The URL

```
POST https://chatgpt.com/backend-api/codex/responses
```

This is NOT `api.openai.com`. It's a separate backend designed for the Codex CLI. Regular OpenAI API keys won't work here. Neither would an API-key user's billing setup.

### The required headers

```
Authorization: Bearer <access_token>
ChatGPT-Account-Id: <account_id>
Content-Type: application/json
Accept: text/event-stream
originator: codex_cli_rs
```

`Accept: text/event-stream` is how we tell the server "I expect SSE, don't try to give me plain JSON." `originator: codex_cli_rs` is a user-agent-like hint that matches the one Codex CLI sends; we impersonate Codex CLI enough to be treated like one. `ChatGPT-Account-Id` is the crucial one — it tells the server which ChatGPT account's subscription should absorb the cost of this request.

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

## Part 7 — Putting it all together

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

## Further reading inside this repo

- [`00-chat-loop/src/mybot/provider/llm/oauth.py`](00-chat-loop/src/mybot/provider/llm/oauth.py) — login + refresh + Token_Store, ~400 lines fully commented.
- [`00-chat-loop/src/mybot/provider/llm/responses.py`](00-chat-loop/src/mybot/provider/llm/responses.py) — the SSE client + aggregator, ~250 lines.
- [`00-chat-loop/src/mybot/provider/llm/base.py`](00-chat-loop/src/mybot/provider/llm/base.py) — the translation layer + LLMProvider.
- [`tests/test_property_refresh_trigger.py`](tests/test_property_refresh_trigger.py) and friends — the Hypothesis property tests that prove the invariants described above.

## External references

- [RFC 7636: PKCE](https://datatracker.ietf.org/doc/html/rfc7636) — the official spec.
- [OAuth 2.0 spec (RFC 6749)](https://datatracker.ietf.org/doc/html/rfc6749) — the broader auth flow this extends.
- [Server-Sent Events spec (whatwg)](https://html.spec.whatwg.org/multipage/server-sent-events.html) — wire format details.
- [OpenAI Codex CLI source](https://github.com/openai/codex) — where we learned the exact scopes, originator, and client_id.
