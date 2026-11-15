#!/usr/bin/env bash
# scripts/smoke_browser.sh — minimal, fast sanity check
set -euo pipefail

TEST_PORT="${TEST_PORT:-5555}"
BASE="http://127.0.0.1:$TEST_PORT"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

python3 "$SCRIPT_DIR/test_server.py" --port "$TEST_PORT" &
PID=$!
trap 'kill $PID 2>/dev/null || true' EXIT
sleep 0.5

# 1. navigate
echo "→ navigate"
server-cli invoke browser.navigate \
    --params "{\"url\":\"$BASE/test.html\"}" \
    | jq -e '.title == "Test Form"' >/dev/null \
    && echo "  ✓ title = Test Form" \
    || { echo "  ✗ title mismatch"; exit 1; }

# 2. fill_form
echo "→ fill_form"
server-cli invoke browser.fill_form --params "{
  \"url\": \"$BASE/test.html\",
  \"fields\": {\"#name\": \"Chethan\", \"#email\": \"c@example.com\"},
  \"submit_selector\": \"button[type=submit]\"
}" | jq -e '.ok == true' >/dev/null \
    && echo "  ✓ ok" \
    || { echo "  ✗ fill_form failed"; exit 1; }

# 3. book (stdin)
echo "→ book (stdin)"
server-cli invoke browser.book <<JSON | jq -e '.text | test("Booking confirmed")' >/dev/null \
    && echo "  ✓ book succeeded" \
    || { echo "  ✗ book failed"; exit 1; }
{
  "start_url": "$BASE/book.html",
  "steps": [
    {"action": "fill", "selector": "#date", "value": "2026-12-01"},
    {"action": "click", "selector": "#search"}
  ]
}
JSON

echo "all smoke tests passed"