from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable


class EventBus:
    def __init__(self):
        self._subscribers = defaultdict(list)

    def subscribe(self, event_name: str, callback: Callable[[Any], None]) -> None:
        self._subscribers[event_name].append(callback)

    def publish(self, event_name: str, payload: Any) -> None:
        for callback in self._subscribers[event_name]:
            callback(payload)

