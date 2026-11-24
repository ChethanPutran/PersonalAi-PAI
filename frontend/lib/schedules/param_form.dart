import 'dart:convert';

/// Turns a capability's JSON Schema into a typed list of form fields, and validates what
/// the user typed against it. Pure Dart (no Flutter), so it is unit-tested directly.
///
/// The server validates again and is authoritative; this only gives early, local feedback.
/// Shapes the form cannot render (arrays, nested objects, oneOf/anyOf/$ref...) fall back to a
/// validated raw-JSON field instead of silently dropping the parameter.
sealed class ParamField {
  final String name;
  final String label;
  final String? description;
  final bool required;
  const ParamField({
    required this.name,
    required this.label,
    this.description,
    this.required = false,
  });

  /// Initial text shown for this field ('' when none).
  String get initialText;

  /// Validates [raw] (already trimmed; '' when empty). Returns `(value, error)`; a null
  /// value with no error means "omit this key".
  (Object?, String?) parse(String raw);

  /// Short constraint hints for the helper line ("1–60 chars", ">= 5").
  String? get hint => null;
}

class TextParam extends ParamField {
  final int? minLength, maxLength;
  final String? pattern;
  final String? defaultValue;
  const TextParam({
    required super.name,
    required super.label,
    super.description,
    super.required,
    this.minLength,
    this.maxLength,
    this.pattern,
    this.defaultValue,
  });

  @override
  String get initialText => defaultValue ?? '';

  @override
  String? get hint {
    final parts = <String>[
      if (minLength != null && maxLength != null) '$minLength–$maxLength characters'
      else if (minLength != null) 'at least $minLength characters'
      else if (maxLength != null) 'up to $maxLength characters',
      if (pattern != null) 'format: $pattern',
    ];
    return parts.isEmpty ? null : parts.join(' · ');
  }

  @override
  (Object?, String?) parse(String raw) {
    if (raw.isEmpty) return (null, null);
    if (minLength != null && raw.length < minLength!) {
      return (null, 'At least $minLength characters');
    }
    if (maxLength != null && raw.length > maxLength!) {
      return (null, 'At most $maxLength characters');
    }
    if (pattern != null) {
      try {
        if (!RegExp(pattern!).hasMatch(raw)) return (null, 'Does not match the required format');
      } on FormatException {
        // A JSON-Schema regex Dart cannot parse: leave it to the server.
      }
    }
    return (raw, null);
  }
}

class NumberParam extends ParamField {
  final bool integer;
  final num? minimum, maximum;
  final num? defaultValue;
  const NumberParam({
    required super.name,
    required super.label,
    super.description,
    super.required,
    this.integer = false,
    this.minimum,
    this.maximum,
    this.defaultValue,
  });

  @override
  String get initialText => defaultValue == null ? '' : _fmt(defaultValue!);

  @override
  String? get hint {
    if (minimum != null && maximum != null) return '${_fmt(minimum!)} to ${_fmt(maximum!)}';
    if (minimum != null) return '≥ ${_fmt(minimum!)}';
    if (maximum != null) return '≤ ${_fmt(maximum!)}';
    return null;
  }

  static String _fmt(num n) => n == n.roundToDouble() ? n.toInt().toString() : n.toString();

  @override
  (Object?, String?) parse(String raw) {
    if (raw.isEmpty) return (null, null);
    final num? v = integer ? int.tryParse(raw) : num.tryParse(raw);
    if (v == null) return (null, integer ? 'Enter a whole number' : 'Enter a number');
    if (v is double && (v.isNaN || v.isInfinite)) return (null, 'Enter a number');
    if (minimum != null && v < minimum!) return (null, 'Must be at least ${_fmt(minimum!)}');
    if (maximum != null && v > maximum!) return (null, 'Must be at most ${_fmt(maximum!)}');
    return (v, null);
  }
}

class BoolParam extends ParamField {
  final bool defaultValue;
  const BoolParam({
    required super.name,
    required super.label,
    super.description,
    super.required,
    this.defaultValue = false,
  });

  @override
  String get initialText => defaultValue.toString();

  @override
  (Object?, String?) parse(String raw) => (raw == 'true', null);
}

class EnumParam extends ParamField {
  /// The allowed values, kept typed (a string, int or double each).
  final List<Object> options;
  final Object? defaultValue;
  const EnumParam({
    required super.name,
    required super.label,
    super.description,
    super.required,
    required this.options,
    this.defaultValue,
  });

  @override
  String get initialText => defaultValue?.toString() ?? '';

  @override
  (Object?, String?) parse(String raw) {
    if (raw.isEmpty) return (null, null);
    for (final o in options) {
      if (o.toString() == raw) return (o, null);
    }
    return (null, 'Choose one of the listed values');
  }
}

/// Fallback for a property the form cannot render natively: edited as JSON text.
class JsonParam extends ParamField {
  final Object? defaultValue;
  final String? expectedType; // 'array' | 'object' | null
  const JsonParam({
    required super.name,
    required super.label,
    super.description,
    super.required,
    this.defaultValue,
    this.expectedType,
  });

  @override
  String get initialText => defaultValue == null ? '' : jsonEncode(defaultValue);

  @override
  String? get hint => expectedType == null ? 'JSON value' : 'JSON $expectedType';

  @override
  (Object?, String?) parse(String raw) {
    if (raw.isEmpty) return (null, null);
    try {
      final v = jsonDecode(raw);
      if (expectedType == 'array' && v is! List) return (null, 'Expected a JSON array, like [1, 2]');
      if (expectedType == 'object' && v is! Map) return (null, 'Expected a JSON object, like {"a": 1}');
      return (v, null);
    } on FormatException catch (e) {
      return (null, 'Invalid JSON: ${e.message}');
    }
  }
}

/// Result of validating the form: typed [values] for valid fields, [errors] by field name.
class ParamCollectResult {
  final Map<String, Object?> values;
  final Map<String, String> errors;
  const ParamCollectResult(this.values, this.errors);
  bool get isValid => errors.isEmpty;
}

class ParamFormSpec {
  /// Name of the single raw-JSON field used when the whole schema is unsupported.
  static const wholeObjectKey = '__parameters__';

  final List<ParamField> fields;

  /// True when the schema could not be mapped to fields: [fields] then holds one
  /// [JsonParam] named [wholeObjectKey] that edits the entire parameters object.
  final bool wholeObjectFallback;

  const ParamFormSpec(this.fields, {this.wholeObjectFallback = false});

  bool get isEmpty => fields.isEmpty;

  static const _complexKeywords = ['oneOf', 'anyOf', 'allOf', r'$ref', 'not', 'if'];

  factory ParamFormSpec.fromSchema(Map<String, dynamic>? schema) {
    if (schema == null || schema.isEmpty) return const ParamFormSpec([]);
    final props = schema['properties'];
    final type = schema['type'];
    final topLevelComplex = _complexKeywords.any(schema.containsKey);
    if ((type != null && type != 'object') || topLevelComplex || (props != null && props is! Map)) {
      return ParamFormSpec.wholeObject(schema);
    }
    if (props == null || (props as Map).isEmpty) {
      // `{"type": "object"}` with free-form keys: nothing to render, but parameters may
      // still be wanted -> raw JSON; an explicit empty `properties` means "takes none".
      return schema.containsKey('properties')
          ? const ParamFormSpec([])
          : ParamFormSpec.wholeObject(schema);
    }
    final requiredNames = (schema['required'] is List)
        ? (schema['required'] as List).map((e) => e.toString()).toSet()
        : <String>{};
    final fields = <ParamField>[
      for (final e in props.entries)
        _fieldFor(e.key.toString(), e.value, requiredNames.contains(e.key.toString())),
    ];
    return ParamFormSpec(fields);
  }

  factory ParamFormSpec.wholeObject(Map<String, dynamic> schema) => ParamFormSpec([
        JsonParam(
          name: wholeObjectKey,
          label: 'Parameters',
          description: schema['description']?.toString(),
          expectedType: 'object',
          defaultValue: const <String, Object?>{},
        ),
      ], wholeObjectFallback: true);

  static ParamField _fieldFor(String name, Object? raw, bool required) {
    final label = _humanize(name);
    if (raw is! Map) {
      return JsonParam(name: name, label: label, required: required);
    }
    final p = raw.cast<String, dynamic>();
    final description = p['description']?.toString();
    final title = p['title']?.toString();
    final shownLabel = (title != null && title.isNotEmpty) ? title : label;
    final type = p['type'];
    final def = p['default'];

    num? numOf(String k) => p[k] is num ? p[k] as num : null;
    int? intOf(String k) => p[k] is num ? (p[k] as num).toInt() : null;

    if (_complexKeywords.any(p.containsKey) || type is List) {
      return JsonParam(name: name, label: shownLabel, description: description,
          required: required, defaultValue: def);
    }
    if (p['enum'] is List && (p['enum'] as List).isNotEmpty) {
      final options = (p['enum'] as List).whereType<Object>().toList();
      if (options.every((o) => o is String || o is num)) {
        return EnumParam(name: name, label: shownLabel, description: description,
            required: required, options: options, defaultValue: def);
      }
      return JsonParam(name: name, label: shownLabel, description: description,
          required: required, defaultValue: def);
    }
    switch (type) {
      case 'string':
        return TextParam(
          name: name, label: shownLabel, description: description, required: required,
          minLength: intOf('minLength'), maxLength: intOf('maxLength'),
          pattern: p['pattern']?.toString(),
          defaultValue: def?.toString(),
        );
      case 'integer':
        return NumberParam(
          name: name, label: shownLabel, description: description, required: required,
          integer: true, minimum: numOf('minimum'), maximum: numOf('maximum'),
          defaultValue: def is num ? def : null,
        );
      case 'number':
        return NumberParam(
          name: name, label: shownLabel, description: description, required: required,
          minimum: numOf('minimum'), maximum: numOf('maximum'),
          defaultValue: def is num ? def : null,
        );
      case 'boolean':
        return BoolParam(name: name, label: shownLabel, description: description,
            required: required, defaultValue: def == true);
      case 'array':
      case 'object':
        return JsonParam(name: name, label: shownLabel, description: description,
            required: required, defaultValue: def, expectedType: type as String);
      default:
        return JsonParam(name: name, label: shownLabel, description: description,
            required: required, defaultValue: def);
    }
  }

  static String _humanize(String name) {
    final spaced = name.replaceAll(RegExp(r'[_\-]+'), ' ').trim();
    if (spaced.isEmpty) return name;
    return spaced[0].toUpperCase() + spaced.substring(1);
  }

  /// Initial raw text per field, preferring [existing] parameter values (edit mode) over
  /// schema defaults.
  Map<String, String> initialRaw([Map<String, dynamic>? existing]) {
    if (wholeObjectFallback) {
      final base = (existing != null && existing.isNotEmpty) ? existing : const <String, Object?>{};
      return {wholeObjectKey: jsonEncode(base)};
    }
    return {
      for (final f in fields)
        f.name: (existing != null && existing.containsKey(f.name) && existing[f.name] != null)
            ? (existing[f.name] is String ? existing[f.name] as String : _encode(f, existing[f.name]))
            : f.initialText,
    };
  }

  static String _encode(ParamField f, Object? v) =>
      (f is JsonParam || v is Map || v is List) ? jsonEncode(v) : v.toString();

  /// Validates [raw] (field name -> typed text; bools as 'true'/'false') and builds the
  /// `parameters` object. Empty optional fields are omitted so server defaults apply.
  ParamCollectResult collect(Map<String, String> raw) {
    final values = <String, Object?>{};
    final errors = <String, String>{};

    if (wholeObjectFallback) {
      final text = (raw[wholeObjectKey] ?? '').trim();
      final (v, err) = fields.single.parse(text.isEmpty ? '{}' : text);
      if (err != null) return ParamCollectResult(const {}, {wholeObjectKey: err});
      return ParamCollectResult(Map<String, Object?>.from(v as Map), const {});
    }

    for (final f in fields) {
      final text = (raw[f.name] ?? '').trim();
      final (v, err) = f.parse(text);
      if (err != null) {
        errors[f.name] = err;
      } else if (v != null) {
        values[f.name] = v;
      } else if (f.required && f is! BoolParam) {
        errors[f.name] = 'Required';
      }
    }
    return ParamCollectResult(values, errors);
  }
}
