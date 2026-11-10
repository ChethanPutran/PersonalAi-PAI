curl -s -X POST http://127.0.0.1:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"chethan@example.com","password":"password123"}' \
  | python -m json.tool