
def file_read_skill(path: str) -> str:
    """Read a file (requires approval)"""
    with open(path, 'r') as f:
        return f.read()

def file_write_skill(path: str, content: str) -> bool:
    """Write to a file (requires approval)"""
    with open(path, 'w') as f:
        f.write(content)
    return True