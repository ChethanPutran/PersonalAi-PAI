# List the catalog visible to your user
uv run --active --env-file .env server-cli list

# Filter by platform/arch
uv run --active --env-file .env server-cli list --platform python --architecture arm64

# Show one plugin's full metadata (schemas, platforms, capabilities)
uv run --active --env-file .env server-cli show camera

# Ask the device to install a plugin (async — see note)
uv run --active --env-file .env server-cli install camera

# Enable for this user + the device (device picks it up on next sync)
uv run --active --env-file .env server-cli enable camera

# Disable
uv run --active --env-file .env server-cli disable camera

# What's actually installed on the device right now
uv run --active --env-file .env server-cli status

# Backend's registry (raw index.json)
uv run --active --env-file .env server-cli registry

# Catalog for a specific platform
uv run --active --env-file .env server-cli catalog --platform python

# Plugin manager health
uv run --active --env-file .env server-cli system

# Override device_id for one call
uv run --active --env-file .env server-cli status --device abc-123

# Machine-readable output for scripts
uv run --active --env-file .env server-cli list --json | jq '.plugins[].id'


# Navigate
server-cli invoke browser.navigate \
  --params '{"url": "http://0.0.0.0:5000/test.html"}'

# Fill a form and submit
server-cli invoke browser.fill_form --params '{
  "url": "http://0.0.0.0:5000/test.html",
  "fields": {"#username": "Chethan", "#email": "c@example.com"},
  "submit_selector": "button[type=submit]"
}'

# Camera still frame
server-cli invoke camera.capture \
  --params '{"outputPath": "/tmp/pic.jpg"}'

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

# Machine-readable output (pipes into jq)
server-cli invoke camera.capture --params '{"format":"jpeg"}' --json \
  | jq '.result.data.path'