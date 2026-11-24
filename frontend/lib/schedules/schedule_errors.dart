/// One problem the server found in a submitted schedule (HTTP 422).
class FieldError {
  final String field; // 'title', 'cron', 'parameters.seconds', ... ('' = not tied to a field)
  final String message;
  const FieldError(this.field, this.message);

  @override
  String toString() => field.isEmpty ? message : '$field: $message';
}

/// Raised by [ScheduleService.create]/`update` when the server rejects the schedule with
/// `422 {"detail": {"errors": [{"field", "message"}]}}`. The server is the authority; the
/// editor maps these onto its inputs and shows the rest in a banner.
class ScheduleValidationException implements Exception {
  final List<FieldError> errors;
  const ScheduleValidationException(this.errors);

  static const _paramPrefix = 'parameters.';

  /// Parses the response body. Understands the contract shape, and tolerates FastAPI's stock
  /// `{"detail": [{"loc": ["body","cron"], "msg": "..."}]}` and a plain `{"detail": "text"}`.
  factory ScheduleValidationException.fromBody(Object? body) {
    final detail = body is Map ? body['detail'] : null;
    final errors = <FieldError>[];

    List<dynamic>? list;
    if (detail is Map && detail['errors'] is List) {
      list = detail['errors'] as List;
    } else if (detail is List) {
      list = detail;
    }
    if (list != null) {
      for (final e in list) {
        if (e is! Map) continue;
        final msg = (e['message'] ?? e['msg'] ?? '').toString();
        var field = (e['field'] ?? '').toString();
        if (field.isEmpty && e['loc'] is List) {
          final loc = (e['loc'] as List).map((x) => x.toString()).toList();
          if (loc.isNotEmpty && loc.first == 'body') loc.removeAt(0);
          field = loc.join('.');
        }
        if (msg.isNotEmpty) errors.add(FieldError(field, msg));
      }
    } else if (detail is String && detail.isNotEmpty) {
      errors.add(FieldError('', detail));
    }
    if (errors.isEmpty) errors.add(const FieldError('', 'The server rejected this schedule.'));
    return ScheduleValidationException(errors);
  }

  /// Messages for one top-level field ('title', 'cron', ...).
  List<String> forField(String field) =>
      [for (final e in errors) if (e.field == field) e.message];

  /// `parameters.<name>` errors keyed by parameter name (first message wins).
  Map<String, String> get parameterErrors => {
        for (final e in errors)
          if (e.field.startsWith(_paramPrefix) && e.field.length > _paramPrefix.length)
            e.field.substring(_paramPrefix.length): e.message,
      };

  /// Everything that is not tied to one of [knownFields] or a parameter — for the banner.
  List<String> unmapped(Set<String> knownFields) => [
        for (final e in errors)
          if (!knownFields.contains(e.field) &&
              !(e.field.startsWith(_paramPrefix) && e.field.length > _paramPrefix.length))
            e.toString(),
      ];

  @override
  String toString() => 'ScheduleValidationException(${errors.join('; ')})';
}
