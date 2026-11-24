from .evaluator import ScopeResult

class DeviceScope:
    @staticmethod
    def evaluate(request, permission):
        device = request.resource.id or request.target_device_id or request.parameters.get("device_id")
        allowed = request.context.get("allowed_devices", [])
        ok = bool(device and device in allowed)
        return ScopeResult(ok, "Device allowed" if ok else "Device is not allowlisted", {"device_id": device})