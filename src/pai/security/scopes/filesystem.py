from pathlib import Path
from .evaluator import ScopeResult

# Every parameter that names a place on disk. ALL of them are checked, so `files.copy`/`files.move`
# cannot smuggle a destination outside the roots and a camera `outputPath` is scoped like a `path`.
PATH_KEYS = ("path", "outputPath", "source", "destination")


def request_paths(parameters) -> list[str]:
    return [str(parameters[k]) for k in PATH_KEYS if parameters.get(k)]


class FilesystemScope:
    @staticmethod
    def evaluate(request, permission):
        given = request_paths(request.parameters)
        rid = request.resource.id
        if rid and given and rid not in given:
            return ScopeResult(False, "Authorized resource does not match 'path' parameter", {})
        paths = list(dict.fromkeys([*([rid] if rid else []), *given]))
        if not paths:
            # The capability may omit the path only if its manifest does not require one (the camera
            # then writes to its own fixed default folder: nothing caller-chosen to contain).
            if request.context.get("filesystem_path_optional"):
                return ScopeResult(True, "No caller-chosen path: the plugin uses its own default location", {})
            return ScopeResult(False, "Filesystem permission requires a path", {})
        allowed = request.context.get("filesystem_roots", [])
        if not allowed:
            return ScopeResult(False, "No filesystem roots configured", {})
        roots = [Path(p).expanduser().resolve(strict=False) for p in allowed]
        targets = [Path(p).expanduser().resolve(strict=False) for p in paths]
        outside = [t for t in targets if not any(t == r or r in t.parents for r in roots)]
        return ScopeResult(
            not outside,
            "Path is inside allowed root" if not outside else "Path is outside allowed roots",
            {"path": str(targets[0]), "paths": [str(t) for t in targets], "roots": [str(r) for r in roots]})
