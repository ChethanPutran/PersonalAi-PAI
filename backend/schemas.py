from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class ChatRequest:
    message: str


@dataclass
class ChatResponse:
    response: str
    plugins: List[str]

