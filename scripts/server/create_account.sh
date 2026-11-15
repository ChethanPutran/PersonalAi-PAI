curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"agent-user@example.com","password":"strong-password-here"}'