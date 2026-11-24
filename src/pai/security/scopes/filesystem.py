from pathlib import Path
from .evaluator import ScopeResult

class FilesystemScope:
    @staticmethod
    def evaluate(request, permission):
        path = request.resource.id
        param = request.parameters.get("path")
        if path and param and param != path:
            return ScopeResult(False, "Authorized resource does not match 'path' parameter", {})
        path = path or param
        if not path:
            return ScopeResult(False, "Filesystem permission requires a path", {})
        target = Path(path).expanduser().resolve(strict=False)
        allowed = request.context.get("filesystem_roots", [])
        if not allowed:
            return ScopeResult(False, "No filesystem roots configured", {})
        roots = [Path(p).expanduser().resolve(strict=False) for p in allowed]
        contained = any(target == root or root in target.parents for root in roots)
        return ScopeResult(contained, "Path is inside allowed root" if contained else "Path is outside allowed roots", {"path": str(target), "roots": [str(r) for r in roots]})