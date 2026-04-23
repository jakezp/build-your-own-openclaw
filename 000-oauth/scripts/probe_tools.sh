#!/bin/bash
# Send a raw curl POST to the ChatGPT Responses API with a tool schema.
# Forces the model to emit a function_call, so you can see the
# response.function_call_arguments.* and response.output_item.done events
# on the wire.
#
# Usage: ./000-oauth/scripts/probe_tools.sh

set -u

PROMPT='run a bash command to echo the string: "hello from probe_tools"'

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

BODY=$(python3 - "$PROMPT" <<'PY'
import json, sys
prompt = sys.argv[1]
body = {
    "model": "gpt-5.4",
    "instructions": "You are a shell helper. Use the `bash` tool to satisfy the user's request.",
    "input": [{"role": "user", "content": prompt}],
    "stream": True,
    "store": False,
    "tools": [
        {
            "type": "function",
            "name": "bash",
            "description": "Execute a bash command and return its stdout.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The shell command to execute."
                    }
                },
                "required": ["command"]
            }
        }
    ]
}
print(json.dumps(body))
PY
)

echo "==== Raw Responses API probe WITH TOOLS ===="
echo "Endpoint: https://chatgpt.com/backend-api/codex/responses"
echo "Prompt:   $PROMPT"
echo
echo "Expect these event types to appear:"
echo "  response.output_item.added   (function_call registered)"
echo "  response.function_call_arguments.delta   (args streaming)"
echo "  response.function_call_arguments.done    (args finalized)"
echo "  response.output_item.done    (call committed)"
echo
echo "--- SSE stream ---"
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
echo "Done. The \`arguments\` string in response.function_call_arguments.done"
echo "is the JSON our aggregator collects into AggregatedToolCall.arguments."
