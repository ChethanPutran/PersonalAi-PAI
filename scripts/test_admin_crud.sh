USER_ID="2f66b7a5-e5c6-4d4d-bc4f-d5b4fad2f265"

source .env
source ./scripts/get_token.sh

echo "USER_ID: $USER_ID"
echo "TOKEN: $TOKEN"

# 1. Make a change
curl -X PATCH http://localhost:8000/api/v1/system/users/$USER_ID \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"is_admin": true}'

# 2. Immediately query the DB — from a SEPARATE shell, not the app
sqlite3 data/pai.db "SELECT id, email, is_admin FROM users WHERE id = '$USER_ID';"

