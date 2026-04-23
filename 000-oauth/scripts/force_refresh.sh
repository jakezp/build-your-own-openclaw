#!/bin/bash
# Force a Token_Store refresh without waiting for natural expiry.
#
# What it does:
#   1. Backs up the Token_Store to <path>.bak
#   2. Rewrites expires_at to 30 seconds in the future
#   3. Records the "before" timestamp
#   4. Sends a one-shot chat message through `my-bot chat` (which triggers
#      access_token() -> needs_refresh() -> _refresh())
#   5. Records the "after" timestamp
#   6. Reports the delta
#
# The "after" should be roughly an hour later than the "before" forced
# value, confirming the refresh ran.
#
# Usage: ./000-oauth/scripts/force_refresh.sh [message]
#
# (The message defaults to "say hi in one word". You'll see it echoed back.)

set -euo pipefail

MSG="${1:-say hi in one word}"

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

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
cd "$REPO_ROOT"

# 1. Back up.
cp "$STORE" "$STORE.bak"
echo "Backed up Token_Store to $STORE.bak"

# 2+3. Record before and rewrite expires_at.
BEFORE=$(python3 - "$STORE" <<'PY'
import json, sys
from datetime import datetime, timedelta, timezone
path = sys.argv[1]
with open(path) as f:
    d = json.load(f)
original = d.get("expires_at", "<none>")
new = datetime.now(timezone.utc) + timedelta(seconds=30)
# Match the ISO-with-offset format pydantic writes.
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

# 4. Send one chat message. `my-bot chat` is interactive; we pipe the
#    message via stdin and an exit command.
echo
echo "Sending chat message to trigger refresh..."
echo
cd 00-chat-loop
printf '%s\nquit\n' "$MSG" | uv run my-bot chat || {
    echo
    echo "Chat failed. Restoring backup."
    mv "$STORE.bak" "$STORE"
    exit 1
}
cd "$REPO_ROOT"

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
