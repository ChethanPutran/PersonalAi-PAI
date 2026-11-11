#!/bin/bash
set -a; source .env; set +a

TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$PAI_ADMIN_EMAILS\",\"password\":\"$PAI_ADMIN_PASS\"}" \
  | jq -r .access_token)

echo "$TOKEN"; echo