# Navigate
server-cli invoke browser.navigate \
  --params '{"url": "https://example.com"}'

# Fill a form and submit
server-cli invoke browser.fill_form --params '{
  "url": "https://example.com/contact",
  "fields": {"#name": "Chethan", "#email": "c@example.com"},
  "submit_selector": "button[type=submit]"
}'

# Pipe params from stdin (nice for scripts / here-docs)
server-cli invoke browser.book <<'JSON'
{
  "start_url": "https://booking.example.com",
  "steps": [
    {"action": "fill", "selector": "#date", "value": "2026-12-01"},
    {"action": "click", "selector": "#search"}
  ]
}
JSON

# Read params from a file
server-cli invoke browser.book --params @steps.json

# Longer timeout for a slow flow
server-cli invoke browser.book --params @steps.json --timeout 180

