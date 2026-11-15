#!/usr/bin/env bash
# scripts/test_browser_plugin.sh
#
# End-to-end tests for the browser plugin via server-cli.
#
# Pre-requisites:
#   - backend running
#   - agent running (server --reload) and connected
#   - browser plugin installed AND enabled (server-cli enable browser)
#   - jq, curl, python3 on PATH
#
# Usage:
#   ./scripts/test_browser_plugin.sh
#   TEST_PORT=6666 ./scripts/test_browser_plugin.sh
#
set -uo pipefail   # NOT -e: we want to keep going on test failures

TEST_HOST="${TEST_HOST:-127.0.0.1}"
TEST_PORT="${TEST_PORT:-5555}"
TEST_BASE="http://${TEST_HOST}:${TEST_PORT}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

PASS=0
FAIL=0
SKIP=0

if [[ -t 1 ]]; then
    C_RESET=$'\033[0m'; C_GREEN=$'\033[32m'; C_RED=$'\033[31m'
    C_YELLOW=$'\033[33m'; C_BOLD=$'\033[1m'
else
    C_RESET=""; C_GREEN=""; C_RED=""; C_YELLOW=""; C_BOLD=""
fi

pass()    { printf "  %s✓%s %s\n" "$C_GREEN" "$C_RESET" "$1"; PASS=$((PASS+1)); }
fail()    { printf "  %s✗%s %s\n" "$C_RED" "$C_RESET" "$1"
            [[ -n "${2:-}" ]] && printf "      %s\n" "$2"; FAIL=$((FAIL+1)); }
skip()    { printf "  %s⊘%s %s — %s\n" "$C_YELLOW" "$C_RESET" "$1" "${2:-skipped}"; SKIP=$((SKIP+1)); }
section() { printf "\n%s%s%s\n" "$C_BOLD" "$1" "$C_RESET"; }

# Run a capability and echo its JSON (the plugin's return value).
# Errors go to stderr; use `2>/dev/null` on call sites if you want
# silent mode. Exit code is ignored at the call site (`|| true`).
invoke() {
    local capability="$1"
    local params_json="$2"
    local timeout="${3:-60}"
    server-cli invoke "$capability" \
        --params "$params_json" \
        --timeout "$timeout" 2>/dev/null
}

# Safe jq extraction — returns "" on missing path or invalid JSON.
jqget() {
    local json="$1" path="$2"
    [[ -z "$json" ]] && { echo ""; return; }
    echo "$json" | jq -r "$path // empty" 2>/dev/null || echo ""
}

cleanup() {
    if [[ -n "${SERVER_PID:-}" ]]; then
        kill "$SERVER_PID" 2>/dev/null || true
        wait "$SERVER_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT

# ----------------------------------------------------------------------
# Pre-flight
# ----------------------------------------------------------------------
section "Pre-flight"

for cmd in jq curl python3 server-cli; do
    if ! command -v "$cmd" >/dev/null; then
        fail "$cmd on PATH" "install it and retry"
        exit 2
    fi
    pass "$cmd on PATH"
done

if ! server-cli status --json >/dev/null 2>&1; then
    fail "backend reachable" "server-cli status failed"
    exit 2
fi
pass "backend reachable"

INSTALLED=$(server-cli installed --json 2>/dev/null || echo '{"plugins":[]}')

if ! echo "$INSTALLED" | jq -e '
    .plugins[] | select(.plugin_id == "browser" and .is_installed)
' >/dev/null 2>&1; then
    fail "browser installed" "run: server-cli install browser --watch"
    exit 2
fi
pass "browser installed"

if ! echo "$INSTALLED" | jq -e '
    .plugins[] | select(.plugin_id == "browser" and .enabled_on_device)
' >/dev/null 2>&1; then
    fail "browser enabled" "run: server-cli enable browser"
    exit 2
fi
pass "browser enabled"

# ----------------------------------------------------------------------
# Test server
# ----------------------------------------------------------------------
section "Test server"

if command -v lsof >/dev/null 2>&1; then
    OLD_PID=$(lsof -ti tcp:"$TEST_PORT" 2>/dev/null || true)
    [[ -n "$OLD_PID" ]] && kill "$OLD_PID" 2>/dev/null || true
fi

python3 "$SCRIPT_DIR/test_server.py" --host "$TEST_HOST" --port "$TEST_PORT" &
SERVER_PID=$!

READY=0
for _ in $(seq 1 50); do
    if curl -sf "$TEST_BASE/healthz" >/dev/null 2>&1; then READY=1; break; fi
    sleep 0.1
done
if [[ $READY -ne 1 ]]; then
    fail "test server on $TEST_BASE" "check port $TEST_PORT is free"
    exit 2
fi
pass "test server listening on $TEST_BASE"

# ----------------------------------------------------------------------
# browser.navigate
# ----------------------------------------------------------------------
section "browser.navigate"

NAV=$(invoke browser.navigate "{\"url\":\"$TEST_BASE/test.html\"}" || true)
echo "  → $(echo "$NAV" | jq -c '{ok, title, status, text: (.text[:60])}' 2>/dev/null || echo "(empty)")"

[[ "$(jqget "$NAV" '.ok')"     == "true"      ]] && pass "returns ok: true"     || fail "returns ok: true" "got: $(jqget "$NAV" '.ok')"
[[ "$(jqget "$NAV" '.title')"  == "Test Form" ]] && pass "title is 'Test Form'" || fail "title is 'Test Form'" "got: $(jqget "$NAV" '.title')"
[[ "$(jqget "$NAV" '.status')" == "200"       ]] && pass "HTTP 200"             || fail "HTTP 200" "got: $(jqget "$NAV" '.status')"

echo "$NAV" | jq -e '.text | test("Test Form")' >/dev/null 2>&1 \
    && pass "text contains 'Test Form'" \
    || fail "text contains 'Test Form'" "text: $(jqget "$NAV" '.text' | head -c 200)"

echo "$NAV" | jq -e '.text | test("Submit")' >/dev/null 2>&1 \
    && pass "text contains form fields" \
    || fail "text contains form fields" ""

# 404 handling
NAV404=$(invoke browser.navigate "{\"url\":\"$TEST_BASE/missing.html\"}" || true)
[[ "$(jqget "$NAV404" '.status')" == "404" ]] \
    && pass "captures HTTP 404" \
    || fail "captures HTTP 404" "got: $(jqget "$NAV404" '.status')"

# ----------------------------------------------------------------------
# browser.fill_form
# ----------------------------------------------------------------------
section "browser.fill_form"

FORM=$(invoke browser.fill_form "{
  \"url\": \"$TEST_BASE/test.html\",
  \"fields\": {\"#name\": \"Chethan\", \"#email\": \"c@example.com\"},
  \"submit_selector\": \"button[type=submit]\",
  \"screenshot\": true
}" || true)
echo "  → $(echo "$FORM" | jq -c '{ok, url, screenshot, text: (.text[:60] // null)}' 2>/dev/null || echo "(empty)")"

[[ "$(jqget "$FORM" '.ok')" == "true" ]] \
    && pass "returns ok: true" \
    || fail "returns ok: true" "got: $(jqget "$FORM" '.ok')"

SHOT=$(jqget "$FORM" '.screenshot')
if [[ -n "$SHOT" && -f "$SHOT" ]]; then
    pass "screenshot written ($SHOT)"
else
    fail "screenshot written" "got: ${SHOT:-(none)}"
fi

# Extended assertions — only run once _fill_form returns .text
# (see the _fill_form rewrite earlier in this thread).
if [[ "$(jqget "$FORM" '.text')" != "" ]]; then
    echo "$FORM" | jq -e '.text | test("Thank you")' >/dev/null 2>&1 \
        && pass "text confirms submission ('Thank you')" \
        || fail "text confirms submission" "text: $(jqget "$FORM" '.text' | head -c 300)"
    echo "$FORM" | jq -e '.text | test("Chethan")' >/dev/null 2>&1 \
        && pass "text echoes submitted name ('Chethan')" \
        || fail "text echoes submitted name" ""
else
    skip "text confirms submission" "_fill_form doesn't return .text yet"
fi

# ----------------------------------------------------------------------
# browser.fill_form — bad selector
# ----------------------------------------------------------------------
section "browser.fill_form — bad selector"

BAD=$(invoke browser.fill_form "{
  \"url\": \"$TEST_BASE/test.html\",
  \"fields\": {\"#does-not-exist\": \"x\"},
  \"submit_selector\": \"button[type=submit]\"
}" 30 || true)

# After the plugin fix, the fill fails in 5s and returns:
#   {"ok": true, "url": ..., "fill_errors": {"#does-not-exist": "..."}}
# So BAD is non-empty and fill_errors is populated.
if echo "$BAD" | jq -e '.fill_errors | length > 0' >/dev/null 2>&1; then
    pass "surfaces missing-selector error (fill_errors populated)"
elif [[ "$(jqget "$BAD" '.ok')" == "false" ]]; then
    pass "surfaces missing-selector error (returned ok: false)"
else
    fail "surfaces missing-selector error" \
         "got: $(echo "$BAD" | head -c 300)"
fi

# ----------------------------------------------------------------------
# browser.book — inline params
# ----------------------------------------------------------------------
section "browser.book — inline params"

BOOK=$(invoke browser.book "{
  \"start_url\": \"$TEST_BASE/book.html\",
  \"steps\": [
    {\"action\": \"fill\", \"selector\": \"#date\", \"value\": \"2026-12-01\"},
    {\"action\": \"click\", \"selector\": \"#search\"}
  ]
}" || true)
echo "  → $(echo "$BOOK" | jq -c '{ok, url, text: (.text[:80])}' 2>/dev/null || echo "(empty)")"

[[ "$(jqget "$BOOK" '.ok')" == "true" ]] \
    && pass "returns ok: true" \
    || fail "returns ok: true" "got: $(jqget "$BOOK" '.ok')"

echo "$BOOK" | jq -e '.text | test("Booking confirmed")' >/dev/null 2>&1 \
    && pass "text contains 'Booking confirmed'" \
    || fail "text contains 'Booking confirmed'" "text: $(jqget "$BOOK" '.text' | head -c 300)"

echo "$BOOK" | jq -e '.text | test("2026-12-01")' >/dev/null 2>&1 \
    && pass "text echoes filled date ('2026-12-01')" \
    || fail "text echoes filled date" "text: $(jqget "$BOOK" '.text' | head -c 300)"

# ----------------------------------------------------------------------
# browser.book — stdin params (matches your snippet)
# ----------------------------------------------------------------------
section "browser.book — stdin params"

BOOK_STDIN=$(server-cli invoke browser.book 2>/dev/null <<JSON || true
{
  "start_url": "$TEST_BASE/book.html",
  "steps": [
    {"action": "fill", "selector": "#date", "value": "2027-01-15"},
    {"action": "click", "selector": "#search"}
  ]
}
JSON
)

echo "$BOOK_STDIN" | jq -e '.text | test("2027-01-15")' >/dev/null 2>&1 \
    && pass "stdin params reach the plugin" \
    || fail "stdin params reach the plugin" "text: $(jqget "$BOOK_STDIN" '.text' | head -c 300)"

# ----------------------------------------------------------------------
# browser.book — @file params (matches your snippet)
# ----------------------------------------------------------------------
section "browser.book — @file params"

PARAMS_FILE=$(mktemp -t pai-params.XXXXXX.json)
cat > "$PARAMS_FILE" <<JSON
{
  "start_url": "$TEST_BASE/book.html",
  "steps": [
    {"action": "fill", "selector": "#date", "value": "2027-02-20"},
    {"action": "click", "selector": "#search"}
  ]
}
JSON

BOOK_FILE=$(server-cli invoke browser.book --params "@$PARAMS_FILE" 2>/dev/null || true)
rm -f "$PARAMS_FILE"

echo "$BOOK_FILE" | jq -e '.text | test("2027-02-20")' >/dev/null 2>&1 \
    && pass "@file params reach the plugin" \
    || fail "@file params reach the plugin" "text: $(jqget "$BOOK_FILE" '.text' | head -c 300)"

# ----------------------------------------------------------------------
# Transport: unknown capability
# ----------------------------------------------------------------------
section "unknown capability"

# The CLI writes plugin-level failures to stderr and exits 1.
# Capture both streams so the test can see them.
UNKNOWN=$(
    server-cli invoke "browser.does_not_exist" \
        --params '{}' --timeout 30 2>&1 || true
)

if echo "$UNKNOWN" | grep -qi "browser.does_not_exist\|unknown\|not provide"; then
    pass "rejects unknown capability"
else
    fail "rejects unknown capability" \
         "got: $(echo "$UNKNOWN" | head -c 300)"
fi

# ----------------------------------------------------------------------
# Summary
# ----------------------------------------------------------------------
section "Summary"
printf "  %spassed: %d%s   %sfailed: %d%s   %sskipped: %d%s\n" \
    "$C_GREEN" "$PASS" "$C_RESET" \
    "$C_RED" "$FAIL" "$C_RESET" \
    "$C_YELLOW" "$SKIP" "$C_RESET"

[[ $FAIL -eq 0 ]] && exit 0 || exit 1