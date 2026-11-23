from datetime import datetime, timezone

class TokenValidator:
    def __init__(self, issuer):
        self.issuer = issuer
    def validate(self, token, subject=None, plugin_id=None, permission=None, action=None, resource=None,
                 capability=None, device_id=None, task_id=None):
        data = self.issuer.decode_verify(token)
        if datetime.now(timezone.utc) >= datetime.fromisoformat(data["expires_at"]):
            raise ValueError("Token expired")
        for key, expected in (("subject", subject), ("plugin_id", plugin_id), ("permission", permission), ("action", action), ("resource", resource),
                                  ("capability", capability), ("device_id", device_id), ("task_id", task_id)):
            if expected is not None and data.get(key) != expected:
                raise ValueError(f"Token {key} mismatch")
        return data