#!/bin/bash
# Force a Token_Store refresh without waiting for natural expiry.
#
# What it does:
#   1. Backs up the Token_Store to <path>.bak
#   2. Rewrites expires_at to 30 seconds in the future (inside the refresh
#      safety margin, so the next call to access_token() will refresh)
#   3. Records the "before" timestamp
#   4. Sends ONE raw POST to the Responses API via curl — this drives
#      oauth.access_token() indirectly via a tiny Python shim that reads
#      the Token_Store, checks expiry, and (because expires_at is now in
#      the margin) refreshes it. Then we send the chat request with the
#      fresh token.
#   5. Records the "after" timestamp
#   6. Reports the delta
#
# The "after" should be roughly an hour later than the "before" forced
# value, confirming the refresh ran.
#
# This script is self-contained — it does NOT require 00-chat-loop to be
# set up with a config.user.yaml.
#
# Usage: ./000-oauth/scripts/force_refresh.sh

set -euo pipefail

STORE=""
if [ -n "${XDG_CONFIG_HOME:-}" ]; then
    STORE="$XDG_CONFIG_HOME/mybot/chatgpt_oauth.json"
else
    STORE="$HOME/.config/mybot/chatgpt_oauth.json"
fi

if [ ! -f "$STORE" ]; then
    echo "Token_Store not found at $STORE"
    echo "Run \`cd 000-oauth && uv run my-bot login\` first."
    exit 1
fi

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
cd "$REPO_ROOT"

# 1. Back up.
cp "$STORE" "$STORE.bak"
echo "Backed up Token_Store to $STORE.bak"

# 2+3. Record before, rewrite expires_at to force refresh on next read.
BEFORE=$(python3 - "$STORE" <<'PY'
import json, sys
from datetime import datetime, timedelta, timezone
path = sys.argv[1]
with open(path) as f:
    d = json.load(f)
original = d.get("expires_at", "<none>")
new = datetime.now(timezone.utc) + timedelta(seconds=30)
# Match pydantic's ISO-with-offset format; either form parses.
d["expires_at"] = new.isoformat().replace("+00:00", "Z")
with open(path, "w") as f:
    json.dump(d, f, indent=2)
print(f"ORIGINAL={original}")
print(f"FORCED={d['expires_at']}")
PY
)
echo "$BEFORE"
ORIGINAL=$(echo "$BEFORE" | grep '^ORIGINAL=' | cut -d= -f2-)
FORCED=$(echo "$BEFORE" | grep '^FORCED=' | cut -d= -f2-)

# 4. Drive the refresh by asking for access_token() — which checks expiry
#    and refreshes if we're inside the safety margin. Then POST one raw
#    chat request via curl. Uses 000-oauth's uv environment so we don't
#    need 00-chat-loop or any config.user.yaml.
echo
echo "Triggering refresh via oauth.access_token()..."
echo

FRESH_TOKEN=$(uv run --directory 000-oauth python - <<'PY' 2>/dev/null | tail -n 1
import asyncio
from mybot.provider.llm.oauth import ChatGPTOAuth

async def main():
    # access_token() checks expires_at and refreshes if inside margin.
    token = await ChatGPTOAuth().access_token()
    print(token)

asyncio.run(main())
PY
) || {
    echo "Refresh failed. Restoring backup."
    mv "$STORE.bak" "$STORE"
    exit 1
}

ACCOUNT_ID=$(python3 -c "import json; print(json.load(open('$STORE'))['account_id'])")

echo "Sending one raw hello via curl..."
echo

RESP=$(curl -sS -N -X POST "https://chatgpt.com/backend-api/codex/responses" \
    -H "Authorization: Bearer $FRESH_TOKEN" \
    -H "ChatGPT-Account-Id: $ACCOUNT_ID" \
    -H "Content-Type: application/json" \
    -H "Accept: text/event-stream" \
    -H "originator: codex_cli_rs" \
    -d '{
        "model": "gpt-5.4",
        "instructions": "You are terse. Reply with one short sentence.",
        "input": [{"role": "user", "content": "Say hello."}],
        "stream": true,
        "store": false
    }' 2>&1 || echo "")

# Pull the final text out of the SSE stream. Look for response.output_text.done.
REPLY=$(echo "$RESP" | python3 - <<'PY'
import json, sys
event_type = None
for line in sys.stdin:
    line = line.rstrip("\n")
    if line.startswith("event:"):
        event_type = line[len("event:"):].strip()
    elif line.startswith("data:") and event_type == "response.output_text.done":
        payload = json.loads(line[len("data:"):].lstrip())
        print(payload.get("text", ""))
        break
PY
)

if [ -z "$REPLY" ]; then
    echo "Chat request failed or produced no response."
    echo "--- raw stream tail ---"
    echo "$RESP" | tail -n 10
    echo "Restoring backup."
    mv "$STORE.bak" "$STORE"
    exit 1
fi

echo "Assistant reply: $REPLY"

# 5+6. Read the new expires_at.
echo
echo "--- Result ---"
AFTER=$(python3 - "$STORE" <<'PY'
import json, sys
with open(sys.argv[1]) as f:
    d = json.load(f)
print(d.get("expires_at", "<none>"))
PY
)
echo "  Original expires_at: $ORIGINAL"
echo "  Forced   expires_at: $FORCED"
echo "  Final    expires_at: $AFTER"
echo

if [ "$AFTER" != "$FORCED" ]; then
    echo "  Refresh succeeded — expires_at was pushed forward."
    rm -f "$STORE.bak"
    echo "  (Backup removed.)"
else
    echo "  Refresh did NOT happen. Something's off."
    echo "  Backup preserved at: $STORE.bak"
    exit 1
fi
