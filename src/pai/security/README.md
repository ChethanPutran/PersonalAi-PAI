# PAI Security subsystem

A modular authorization starter package for plugin/task/device execution.

## Important
This is a baseline implementation, not a production security boundary yet. The default consent and audit stores are in-memory; persist them, protect the signing secret, harden scope checks, and add tests before production deployment. Authorization requests should be rechecked immediately before execution.

## Quick start
```python
from pai.security.manager import SecurityManager
from pai.security.authorization.request import AuthorizationRequest

security = SecurityManager.create_default()
security.authorization.consent_manager.grant(
    subject="user:local", plugin_id="file-reader", permission="filesystem.read"
)
request = AuthorizationRequest(
    subject="user:local", plugin_id="file-reader", permission="filesystem.read",
    action="read", resource="/workspace/notes.txt",
    parameters={"path": "/workspace/notes.txt"},
    context={"filesystem_roots": ["/workspace"]},
)
decision = security.authorization.authorize(request)
print(decision.effect, decision.reason)
```

## Package integration
Place this folder under your project as `pai/security`. In your execution pipeline, call `authorize()` and require `decision.allowed` before dispatching. Do not rely on the authorization token alone unless you validate it at the execution boundary.

## Built-in permissions
- `filesystem.read` (read)
- `filesystem.write` (write)
- `network.request` (request)
- `device.control` (control)
- `system.execute` (execute)
- `notifications.send` (send)

All permissions require a plugin id. File/network/device scopes require configured allowlists in request.context.