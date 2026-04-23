#!/bin/bash
# Inspect the Token_Store without leaking secret values.
#
# Prints:
#   * the file path
#   * POSIX mode
#   * which fields are present
#   * the expires_at timestamp and how far in the future it is
#
# Does NOT print any access_token / refresh_token / id_token values.
#
# Usage: ./000-oauth/scripts/inspect_token_store.sh

set -u

STORE=""
if [ -n "${XDG_CONFIG_HOME:-}" ]; then
    STORE="$XDG_CONFIG_HOME/mybot/chatgpt_oauth.json"
else
    STORE="$HOME/.config/mybot/chatgpt_oauth.json"
fi

echo "==== Token_Store: $STORE ===="
echo

if [ ! -f "$STORE" ]; then
    echo "Not found."
    echo "Run \`cd 00-chat-loop && uv run my-bot login\` to create it."
    exit 1
fi

echo "--- POSIX mode ---"
if command -v stat >/dev/null 2>&1; then
    # BSD stat (macOS) and GNU stat (Linux) have different flags.
    if stat -f "%Sp %Mp%Lp" "$STORE" >/dev/null 2>&1; then
        # macOS
        stat -f "%Sp  (%Mp%Lp)" "$STORE"
    else
        # Linux
        stat -c "%A  (%a)" "$STORE"
    fi
fi
echo

echo "--- Fields present ---"
python3 - "$STORE" <<'PY'
import json, sys
from datetime import datetime, timezone

path = sys.argv[1]
with open(path) as f:
    d = json.load(f)

expected = ("access_token", "refresh_token", "expires_at", "account_id", "id_token")
for k in expected:
    mark = "present" if d.get(k) else "MISSING"
    print(f"  {k:<16} {mark}")

extra = sorted(set(d) - set(expected))
if extra:
    print(f"\n  Extra keys (unexpected but harmless): {extra}")
PY
echo

echo "--- Expiry ---"
python3 - "$STORE" <<'PY'
import json, sys
from datetime import datetime, timezone

path = sys.argv[1]
with open(path) as f:
    d = json.load(f)

expires_at = d.get("expires_at", "")
print(f"  expires_at: {expires_at}")

# pydantic writes the ISO timestamp with offset. Python 3.11 fromisoformat
# handles "+00:00" fine but some older Zulu strings used 'Z'. Handle both.
raw = expires_at.replace("Z", "+00:00")
try:
    ea = datetime.fromisoformat(raw)
except ValueError:
    print("  (could not parse expiry timestamp)")
    sys.exit()

now = datetime.now(timezone.utc)
if ea.tzinfo is None:
    ea = ea.replace(tzinfo=timezone.utc)

delta = ea - now
print(f"  now:        {now.isoformat(timespec='seconds')}")
if delta.total_seconds() > 0:
    print(f"  valid for:  {delta}")
else:
    print(f"  EXPIRED by: {-delta}")
PY
echo

echo "--- Account id (safe to display) ---"
python3 - "$STORE" <<'PY'
import json, sys
with open(sys.argv[1]) as f:
    d = json.load(f)
print(f"  {d.get('account_id') or '<missing>'}")
PY

echo
echo "Done."
