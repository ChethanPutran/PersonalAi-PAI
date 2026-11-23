class InMemoryConsentRepository:
    def __init__(self):
        self._records = {}
    def put(self, record):
        self._records[(record.subject, record.plugin_id, record.permission)] = record
    def get(self, subject, plugin_id, permission):
        return self._records.get((subject, plugin_id, permission))