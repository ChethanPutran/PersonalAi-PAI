from .models import ConsentRecord
from .validator import ConsentValidator

class ConsentManager:
    def __init__(self, repository):
        self.repository = repository
        
    def grant(self, subject, plugin_id, permission, expires_at=None):
        record = ConsentRecord(subject, plugin_id, permission, True, expires_at)
        self.repository.put(record)
        return record
    
    def revoke(self, subject, plugin_id, permission):
        record = ConsentRecord(subject, plugin_id, permission, False)
        self.repository.put(record)
        return record
    
    def check(self, request, declaration, *, manifest_requires_confirmation: bool = False):
        record = self.repository.get(request.principal_key, request.plugin_id, request.permission)
        # The manifest can only tighten: either side requiring consent is enough.
        if not (declaration.requires_consent or manifest_requires_confirmation):
            return record or ConsentRecord(request.principal_key, request.plugin_id, request.permission, True)
        # Validator also checks the record really belongs to this principal/plugin/permission.
        if ConsentValidator.validate(record, request):
            return record
        return ConsentRecord(request.principal_key, request.plugin_id, request.permission, False, approval_required=True)