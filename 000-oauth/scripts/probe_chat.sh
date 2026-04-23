#!/bin/bash
# Send a raw curl POST to the ChatGPT Responses API, bypassing our Python
# layer entirely. Shows you the SSE event stream as it arrives.
#
# Reads access_token + account_id from the Token_Store.
#
# Usage: ./000-oauth/scripts/probe_chat.sh [prompt]
#
# Default prompt: "say hi in one word"

set -u

PROMPT="${1:-say hi in one word}"

STORE=""
if [ -n "${XDG_CONFIG_HOME:-}" ]; then
    STORE="$XDG_CONFIG_HOME/mybot/chatgpt_oauth.json"
else
    STORE="$HOME/.config/mybot/chatgpt_oauth.json"
fi

if [ ! -f "$STORE" ]; then
    echo "Token_Store not found at $STORE"
    echo "Run \`cd 00-chat-loop && uv run my-bot login\` first."
    exit 1
fi

TOKEN=$(python3 -c "import json; print(json.load(open('$STORE'))['access_token'])")
ACCOUNT=$(python3 -c "import json; print(json.load(open('$STORE'))['account_id'])")
MODEL="gpt-5.4"

BODY=$(python3 - "$PROMPT" <<'PY'
import json, sys
prompt = sys.argv[1]
body = {
    "model": "gpt-5.4",
    "instructions": "You are a terse assistant. Follow instructions exactly.",
    "input": [{"role": "user", "content": prompt}],
    "stream": True,
    "store": False,
}
print(json.dumps(body))
PY
)

echo "==== Raw Responses API probe ===="
echo "Endpoint: https://chatgpt.com/backend-api/codex/responses"
echo "Model:    $MODEL"
echo "Account:  $ACCOUNT"
echo "Prompt:   $PROMPT"
echo
echo "--- SSE stream (press Ctrl+C to abort) ---"
echo

curl -sS -N -X POST "https://chatgpt.com/backend-api/codex/responses" \
  -H "Authorization: Bearer $TOKEN" \
  -H "ChatGPT-Account-Id: $ACCOUNT" \
  -H "Content-Type: application/json" \
  -H "Accept: text/event-stream" \
  -H "originator: codex_cli_rs" \
  -d "$BODY" \
  -w "\n----HTTP %{http_code}\n"

echo
echo "Done. Look above for the full SSE stream."
