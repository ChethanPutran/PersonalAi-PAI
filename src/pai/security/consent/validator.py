class ConsentValidator:
    @staticmethod
    def validate(record, request):
        return bool(record and record.active() and\
                     record.subject == request.principal_key and \
                        record.plugin_id == request.plugin_id and\
                              record.permission == request.permission)