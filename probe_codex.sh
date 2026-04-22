#!/bin/bash
set -u
TOKEN=$(python3 -c "import json; print(json.load(open('$HOME/.config/mybot/chatgpt_oauth.json'))['access_token'])")
ACCOUNT=$(python3 -c "import json; print(json.load(open('$HOME/.config/mybot/chatgpt_oauth.json'))['account_id'])")

probe() {
  local label="$1"; shift
  local body="$1"; shift
  echo "=== $label ==="
  # Use -N so curl streams output without buffering.
  curl -sS -N -X POST "https://chatgpt.com/backend-api/codex/responses" \
    -H "Authorization: Bearer $TOKEN" \
    -H "ChatGPT-Account-Id: $ACCOUNT" \
    -H "Content-Type: application/json" \
    -H "Accept: text/event-stream" \
    -H "originator: codex_cli_rs" \
    -d "$body" \
    -w "\n----HTTP %{http_code}\n" | head -c 4000
  echo; echo
}

# A: gpt-5.4, stream=true, store=false, plain string user content
probe "A: gpt-5.4 stream+store=false plain-string content" \
  '{"model":"gpt-5.4","instructions":"You are helpful.","input":[{"role":"user","content":"Say hi in exactly 3 words."}],"stream":true,"store":false}'

# B: gpt-5.4, content parts form
probe "B: gpt-5.4 content parts form" \
  '{"model":"gpt-5.4","instructions":"You are helpful.","input":[{"role":"user","content":[{"type":"input_text","text":"Say hi in exactly 3 words."}]}],"stream":true,"store":false}'

# C: gpt-5.4-mini
probe "C: gpt-5.4-mini" \
  '{"model":"gpt-5.4-mini","instructions":"You are helpful.","input":[{"role":"user","content":"Say hi in exactly 3 words."}],"stream":true,"store":false}'

# D: attempt with a simple function tool to see if tools are accepted
probe "D: gpt-5.4 with a function tool declared" \
  '{"model":"gpt-5.4","instructions":"You are helpful. Use the tool if asked.","input":[{"role":"user","content":"What is the weather? Use the tool."}],"stream":true,"store":false,"tools":[{"type":"function","name":"get_weather","description":"Get weather","parameters":{"type":"object","properties":{"city":{"type":"string"}},"required":["city"]}}]}'
