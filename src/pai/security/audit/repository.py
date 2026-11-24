class InMemoryAuditRepository:
    """Recent audit events in memory. Bounded: an always-on server must not grow without limit
    (older events are dropped; a durable sink belongs in a DB-backed repository)."""

    MAX_EVENTS = 10_000

    def __init__(self, max_events: int = MAX_EVENTS):
        self.events = []
        self.max_events = max_events

    def append(self, event):
        self.events.append(event)
        if len(self.events) > self.max_events:
            del self.events[: len(self.events) - self.max_events]

    def query(self, limit=100):
        return self.events[-limit:]
