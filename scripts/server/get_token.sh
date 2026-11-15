#!/bin/bash
set -a; source .env; set +a
source .env

TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$PAI_EMAIL\",\"password\":\"$PAI_PASSWORD\"}" \
  | jq -r .access_token)

echo "$TOKEN"; echo