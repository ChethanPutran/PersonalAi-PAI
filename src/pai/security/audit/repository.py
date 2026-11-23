class InMemoryAuditRepository:
    def __init__(self):
        self.events = []
    def append(self, event):
        self.events.append(event)
    def query(self, limit=100):
        return self.events[-limit:]