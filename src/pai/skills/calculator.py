import requests
from typing import Dict, Any

# Built-in skills (these would be separate files in skills/)
def execute(expression: str) -> float:
    """Safe calculator skill"""
    allowed_chars = set("0123456789+-*/(). ")
    if not all(c in allowed_chars for c in expression):
        raise ValueError("Invalid characters in expression")
    return eval(expression)

