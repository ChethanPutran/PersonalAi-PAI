from urllib.parse import urlparse
from .evaluator import ScopeResult

class NetworkScope:
    @staticmethod
    def evaluate(request, permission):
        url = request.resource.id
        param = request.parameters.get("url")
        if url and param and param != url:
            return ScopeResult(False, "Authorized resource does not match 'url' parameter", {})
        url = url or param
        if not url:
            return ScopeResult(False, "Network permission requires a url", {})
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return ScopeResult(False, "Only valid HTTP(S) URLs are permitted", {})
        allow = request.context.get("network_hosts", [])
        if not allow:
            return ScopeResult(False, "No network hosts configured", {})
        host = parsed.hostname.lower()
        allowed = any(host == item.lower() or host.endswith("." + item.lower()) for item in allow)
        return ScopeResult(allowed, "Host allowed" if allowed else "Host not in allowlist", {"host": host})