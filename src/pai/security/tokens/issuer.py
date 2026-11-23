import base64, hashlib, hmac, json, os
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from .models import SecurityToken

class TokenIssuer:
    def __init__(self, secret=None, ttl_seconds=300):
        self.secret = secret or os.urandom(32)
        self.ttl_seconds = ttl_seconds
    def issue(self, request, permission, scope_result, risk):
        now = datetime.now(timezone.utc)
        data = {"token_id": str(uuid4()), "subject": request.subject.key, "on_behalf_of": [a.key for a in request.on_behalf_of], "plugin_id": request.plugin_id,
            "permission": request.permission, "action": request.action.value, "resource": request.resource.uri, "task_id": request.task_id, "capability": request.capability, "device_id": request.target_device_id, "request_id": request.request_id,
            "issued_at": now.isoformat(), "expires_at": (now + timedelta(seconds=self.ttl_seconds)).isoformat(),
            "scope": dict(scope_result.effective_scope), "risk": str(getattr(risk, "level", "low"))}
        payload = json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
        sig = hmac.new(self.secret, payload, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(payload).decode().rstrip("=") + "." + base64.urlsafe_b64encode(sig).decode().rstrip("=")
    def decode_verify(self, token):
        payload_b64, sig_b64 = token.split(".", 1)
        payload = base64.urlsafe_b64decode(payload_b64 + "=" * (-len(payload_b64) % 4))
        sig = base64.urlsafe_b64decode(sig_b64 + "=" * (-len(sig_b64) % 4))
        expected = hmac.new(self.secret, payload, hashlib.sha256).digest()
        if not hmac.compare_digest(sig, expected):
            raise ValueError("Invalid token signature")
        return json.loads(payload)