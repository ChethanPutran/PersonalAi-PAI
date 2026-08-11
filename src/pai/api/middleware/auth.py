from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware

class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Skip auth for health and docs
        if request.url.path in ["/", "/health", "/docs", "/openapi.json"]:
            return await call_next(request)
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Missing or invalid token")
        # Validate token via kernel.security_manager
        token = auth_header.split(" ")[1]
        # For demo, accept any non‑empty token
        if not token:
            raise HTTPException(status_code=401)
        return await call_next(request)

async def get_current_user(request: Request):
    return {"user_id": "demo_user"}