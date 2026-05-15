
def file_read_skill(path: str) -> str:
    """Read a file (requires approval)"""
    with open(path, 'r') as f:
        return f.read()

def file_write_skill(path: str, content: str) -> bool:
    """Write to a file (requires approval)"""
    with open(path, 'w') as f:
        f.write(content)
    return True
from pathlib import Path
from typing import Dict, Any


def file_read_skill(path: str) -> str:
    """Read a file."""
    return Path(path).read_text()


def file_write_skill(path: str, content: str) -> bool:
    """Write to a file."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    return True


def execute(action: str, path: str, content: str = "") -> Dict[str, Any]:
    """Dispatch file operations for the agent."""
    if action == "read":
        return {"action": action, "path": path, "content": file_read_skill(path)}
    if action == "write":
        return {"action": action, "path": path, "written": file_write_skill(path, content)}
    if action == "exists":
        return {"action": action, "path": path, "exists": Path(path).exists()}
    raise ValueError(f"Unsupported file action: {action}")