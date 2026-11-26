// Typed models for confirmation scopes, grants (consents), preflight and device invoke.
//
// Parsing is deliberately tolerant — an unknown enum value or a missing field never throws —
// but it is tolerant in the SAFE direction: an unknown risk is treated as `high`, an unknown
// preflight decision as `deny`, a missing scope list as "once only".
import 'package:flutter/foundation.dart';

// ---------------------------------------------------------------------------
// Risk
// ---------------------------------------------------------------------------

enum ConfirmationRisk {
  low,
  medium,
  high,
  critical;

  /// Unknown values are shown cautiously (as [high]).
  static ConfirmationRisk fromWire(Object? raw) {
    final s = raw?.toString().toLowerCase();
    for (final r in values) {
      if (r.name == s) return r;
    }
    return ConfirmationRisk.high;
  }

  String get label => name;
  bool get isElevated => this == high || this == critical;
}

// ---------------------------------------------------------------------------
// Scope & binding
// ---------------------------------------------------------------------------

enum ConfirmationScope {
  once,
  session,
  always;

  static ConfirmationScope? tryParse(Object? raw) {
    final s = raw?.toString().toLowerCase();
    for (final v in values) {
      if (v.name == s) return v;
    }
    return null;
  }
}

enum BindingMode {
  any,
  exact,
  keys;

  static BindingMode fromWire(Object? raw) {
    final s = raw?.toString().toLowerCase();
    for (final v in values) {
      if (v.name == s) return v;
    }
    return BindingMode.exact; // the narrowest sensible reading
  }
}

/// What a standing approval is tied to: exact parameters, a subset of parameter names, or any.
@immutable
class ParamBinding {
  const ParamBinding({this.mode = BindingMode.exact, this.keys = const []});

  final BindingMode mode;
  final List<String> keys;

  static const exact = ParamBinding();
  static const any = ParamBinding(mode: BindingMode.any);

  factory ParamBinding.fromJson(Object? json) {
    if (json is! Map) return ParamBinding.exact;
    final rawKeys = json['keys'];
    return ParamBinding(
      mode: BindingMode.fromWire(json['mode']),
      keys: rawKeys is List ? rawKeys.map((e) => e.toString()).toList() : const [],
    );
  }

  Map<String, dynamic> toJson() => {'mode': mode.name, 'keys': keys};

  @override
  bool operator ==(Object other) =>
      other is ParamBinding && other.mode == mode && listEquals(other.keys, keys);

  @override
  int get hashCode => Object.hash(mode, Object.hashAll(keys));
}

// ---------------------------------------------------------------------------
// Confirmation request (what the user is asked to approve)
// ---------------------------------------------------------------------------

@immutable
class ParamSummary {
  const ParamSummary({required this.name, required this.valuePreview});
  final String name;
  final String valuePreview;

  factory ParamSummary.fromJson(Object? json) {
    if (json is Map) {
      return ParamSummary(
        name: (json['name'] ?? '').toString(),
        valuePreview: (json['value_preview'] ?? json['value'] ?? '').toString(),
      );
    }
    return ParamSummary(name: '', valuePreview: json?.toString() ?? '');
  }
}

@immutable
class ConfirmationRequest {
  const ConfirmationRequest({
    required this.capability,
    this.pluginId,
    this.deviceId,
    this.deviceName,
    this.risk = ConfirmationRisk.high,
    this.parametersSummary = const [],
    this.allowedScopes = const [ConfirmationScope.once],
    this.canBindParams = false,
    this.defaultBinding = ParamBinding.exact,
    this.requiredBindingKeys = const [],
    this.reason,
  });

  final String capability;
  final String? pluginId;
  final String? deviceId;

  /// Optional friendly name (the client knows it even if the server only sends an id).
  final String? deviceName;
  final ConfirmationRisk risk;
  final List<ParamSummary> parametersSummary;

  /// Never empty: "once" is always permitted.
  final List<ConfirmationScope> allowedScopes;
  final bool canBindParams;
  final ParamBinding defaultBinding;

  /// Parameters a remembered approval must be tied to (shell: the command). 'Any' is never offered.
  final List<String> requiredBindingKeys;
  final String? reason;

  bool get allowsOnly => allowedScopes.length == 1;
  bool allows(ConfirmationScope s) => allowedScopes.contains(s);

  List<String> get parameterNames => [for (final p in parametersSummary) p.name];

  /// Parses `confirmation: {...}`. Anything missing falls back to the most restrictive reading.
  factory ConfirmationRequest.fromJson(Object? json, {String? fallbackCapability}) {
    if (json is! Map) {
      return ConfirmationRequest(capability: fallbackCapability ?? 'unknown action');
    }
    final scopes = <ConfirmationScope>[];
    final rawScopes = json['allowed_scopes'];
    if (rawScopes is List) {
      for (final s in rawScopes) {
        final p = ConfirmationScope.tryParse(s);
        if (p != null && !scopes.contains(p)) scopes.add(p);
      }
    }
    if (!scopes.contains(ConfirmationScope.once)) scopes.insert(0, ConfirmationScope.once);
    final rawParams = json['parameters_summary'];
    final reason = json['reason']?.toString();
    return ConfirmationRequest(
      capability: (json['capability'] ?? fallbackCapability ?? 'unknown action').toString(),
      pluginId: json['plugin_id']?.toString(),
      deviceId: json['device_id']?.toString(),
      risk: ConfirmationRisk.fromWire(json['risk']),
      parametersSummary:
          rawParams is List ? rawParams.map(ParamSummary.fromJson).toList() : const [],
      allowedScopes: scopes,
      canBindParams: json['can_bind_params'] == true,
      defaultBinding: ParamBinding.fromJson(json['default_binding']),
      requiredBindingKeys: [
        for (final k in (json['required_binding_keys'] is List ? json['required_binding_keys'] as List : const []))
          k.toString()
      ],
      reason: (reason == null || reason.isEmpty) ? null : reason,
    );
  }

  /// Same request, restricted to a subset of scopes (the local-tester path offers only
  /// once/session). "once" is always kept.
  ConfirmationRequest restrictedTo(Set<ConfirmationScope> allowed) => ConfirmationRequest(
        capability: capability,
        pluginId: pluginId,
        deviceId: deviceId,
        deviceName: deviceName,
        risk: risk,
        parametersSummary: parametersSummary,
        allowedScopes: [
          for (final s in allowedScopes)
            if (s == ConfirmationScope.once || allowed.contains(s)) s
        ],
        canBindParams: canBindParams,
        defaultBinding: defaultBinding,
        requiredBindingKeys: requiredBindingKeys,
        reason: reason,
      );

  ConfirmationRequest withDeviceName(String? name) => ConfirmationRequest(
        capability: capability,
        pluginId: pluginId,
        deviceId: deviceId,
        deviceName: name ?? deviceName,
        risk: risk,
        parametersSummary: parametersSummary,
        allowedScopes: allowedScopes,
        canBindParams: canBindParams,
        defaultBinding: defaultBinding,
        requiredBindingKeys: requiredBindingKeys,
        reason: reason,
      );

  /// Builds a request from a bare parameters map (legacy chat events that carry no
  /// `confirmation` block): "once" only, nothing is bindable.
  factory ConfirmationRequest.legacy({
    required String capability,
    required Map<String, dynamic> parameters,
    String? reason,
  }) =>
      ConfirmationRequest(
        capability: capability,
        parametersSummary: [
          for (final e in parameters.entries)
            ParamSummary(name: e.key, valuePreview: _preview(e.value)),
        ],
        reason: (reason == null || reason.isEmpty) ? null : reason,
      );

  static String _preview(Object? v) {
    final s = v is String ? v : v.toString();
    return s.length > 120 ? '${s.substring(0, 117)}…' : s;
  }
}

// ---------------------------------------------------------------------------
// The user's decision (+ the exact request body for POST /tasks/{id}/confirm)
// ---------------------------------------------------------------------------

/// Expiry choices for "Always allow", in days. 90 is preselected.
const List<int> kAlwaysAllowDays = [30, 90, 365];
const int kDefaultAlwaysAllowDays = 90;

@immutable
class ConfirmationDecision {
  const ConfirmationDecision.once()
      : scope = ConfirmationScope.once,
        binding = null,
        days = null;

  const ConfirmationDecision.session({this.binding = ParamBinding.exact})
      : scope = ConfirmationScope.session,
        days = null;

  const ConfirmationDecision.always({
    this.binding = ParamBinding.exact,
    this.days = kDefaultAlwaysAllowDays,
  }) : scope = ConfirmationScope.always;

  final ConfirmationScope scope;
  final ParamBinding? binding;
  final int? days;

  /// Body of POST /tasks/{id}/confirm. `binding` only appears for session/always grants
  /// (when the server said parameters can be bound); `days` only for "always".
  Map<String, dynamic> toBody() => {
        'scope': scope.name,
        if (scope != ConfirmationScope.once && binding != null) 'binding': binding!.toJson(),
        if (scope == ConfirmationScope.always && days != null) 'days': days,
      };

  @override
  bool operator ==(Object other) =>
      other is ConfirmationDecision &&
      other.scope == scope &&
      other.binding == binding &&
      other.days == days;

  @override
  int get hashCode => Object.hash(scope, binding, days);
}

// ---------------------------------------------------------------------------
// Errors from the confirmation endpoints
// ---------------------------------------------------------------------------

/// 409 / 422 from confirm: `{"detail": {"code": ..., "message": ...}}` (or a plain string).
class ConfirmationException implements Exception {
  const ConfirmationException(this.message, {this.code, this.statusCode});
  final String message;
  final String? code;
  final int? statusCode;

  /// Extracts the server's message from an ApiException body; never throws.
  factory ConfirmationException.fromBody(int statusCode, Object? body) {
    String? code;
    String? message;
    if (body is Map) {
      final detail = body['detail'];
      if (detail is Map) {
        code = detail['code']?.toString();
        message = detail['message']?.toString();
      } else if (detail is String) {
        message = detail;
      } else if (detail is List && detail.isNotEmpty) {
        final first = detail.first;
        message = first is Map ? (first['msg'] ?? first['message'])?.toString() : first.toString();
      }
    }
    return ConfirmationException(
      message ?? 'The server rejected this approval (HTTP $statusCode).',
      code: code,
      statusCode: statusCode,
    );
  }

  @override
  String toString() => message;
}

// ---------------------------------------------------------------------------
// Standing grants (GET /consents)
// ---------------------------------------------------------------------------

@immutable
class ConsentGrant {
  const ConsentGrant({
    required this.id,
    required this.scope,
    required this.capability,
    this.pluginId = '',
    this.deviceId,
    this.deviceName,
    this.binding = ParamBinding.exact,
    this.paramsPreview,
    this.sessionId,
    this.createdAt,
    this.expiresAt,
    this.lastUsedAt,
    this.useCount = 0,
    this.risk = ConfirmationRisk.high,
  });

  final String id;
  final ConfirmationScope scope; // session | always
  final String pluginId;
  final String capability;
  final String? deviceId;
  final String? deviceName;
  final ParamBinding binding;
  final String? paramsPreview;
  final String? sessionId;
  final DateTime? createdAt;
  final DateTime? expiresAt;
  final DateTime? lastUsedAt;
  final int useCount;
  final ConfirmationRisk risk;

  String get deviceLabel => deviceName ?? deviceId ?? 'any device';

  /// "exact params: path=/tmp/x" / "keys: path, name" / "any params"
  String get bindingSummary => switch (binding.mode) {
        BindingMode.any => 'any params',
        BindingMode.keys =>
          binding.keys.isEmpty ? 'keys: (none)' : 'keys: ${binding.keys.join(', ')}',
        BindingMode.exact => (paramsPreview == null || paramsPreview!.isEmpty)
            ? 'exact params'
            : 'exact params: $paramsPreview',
      };

  static DateTime? _date(Object? raw) =>
      raw == null ? null : DateTime.tryParse(raw.toString());

  static int _int(Object? raw) => raw is int ? raw : int.tryParse('${raw ?? ''}') ?? 0;

  /// Returns null for an entry that has no id (it could not be revoked anyway).
  static ConsentGrant? tryParse(Object? json) {
    if (json is! Map) return null;
    final id = json['id']?.toString();
    if (id == null || id.isEmpty) return null;
    return ConsentGrant(
      id: id,
      // An unrecognised scope is shown as the broader one so it is never understated.
      scope: ConfirmationScope.tryParse(json['scope']) ?? ConfirmationScope.always,
      pluginId: (json['plugin_id'] ?? '').toString(),
      capability: (json['capability'] ?? 'unknown').toString(),
      deviceId: json['device_id']?.toString(),
      deviceName: json['device_name']?.toString(),
      binding: ParamBinding.fromJson(json['binding']),
      paramsPreview: json['params_preview']?.toString(),
      sessionId: json['session_id']?.toString(),
      createdAt: _date(json['created_at']),
      expiresAt: _date(json['expires_at']),
      lastUsedAt: _date(json['last_used_at']),
      useCount: _int(json['use_count']),
      risk: ConfirmationRisk.fromWire(json['risk']),
    );
  }

  ConsentGrant withDeviceName(String? name) => ConsentGrant(
        id: id,
        scope: scope,
        capability: capability,
        pluginId: pluginId,
        deviceId: deviceId,
        deviceName: name ?? deviceName,
        binding: binding,
        paramsPreview: paramsPreview,
        sessionId: sessionId,
        createdAt: createdAt,
        expiresAt: expiresAt,
        lastUsedAt: lastUsedAt,
        useCount: useCount,
        risk: risk,
      );
}

// ---------------------------------------------------------------------------
// Preflight & invoke
// ---------------------------------------------------------------------------

enum PreflightDecision {
  allow,
  confirm,
  deny;

  /// Unknown => deny (fail closed).
  static PreflightDecision fromWire(Object? raw) {
    final s = raw?.toString().toLowerCase();
    for (final v in values) {
      if (v.name == s) return v;
    }
    return PreflightDecision.deny;
  }
}

@immutable
class PreflightResult {
  const PreflightResult({required this.decision, this.reason, this.confirmation});
  final PreflightDecision decision;
  final String? reason;
  final ConfirmationRequest? confirmation;

  factory PreflightResult.fromJson(Object? json) {
    if (json is! Map) {
      return const PreflightResult(
        decision: PreflightDecision.deny,
        reason: 'The server sent an unreadable answer.',
      );
    }
    return PreflightResult(
      decision: PreflightDecision.fromWire(json['decision']),
      reason: json['reason']?.toString(),
      confirmation: json['confirmation'] is Map
          ? ConfirmationRequest.fromJson(json['confirmation'])
          : null,
    );
  }
}

/// Task states the tester treats as "finished".
bool isTerminalTaskStatus(String? status) =>
    status == 'completed' || status == 'failed' || status == 'cancelled';

/// Outcome of POST /devices/{id}/invoke.
@immutable
class InvokeOutcome {
  const InvokeOutcome({
    this.taskId,
    this.status,
    this.result,
    this.awaitingConfirmation = false,
    this.confirmation,
    this.error,
  });

  final String? taskId;
  final String? status;
  final Object? result;
  final bool awaitingConfirmation;
  final ConfirmationRequest? confirmation;
  final String? error;

  bool get finished => isTerminalTaskStatus(status) || (result != null && !awaitingConfirmation);

  factory InvokeOutcome.fromJson(Object? json, {String? fallbackCapability}) {
    if (json is! Map) return const InvokeOutcome();
    final awaiting = json['awaiting_confirmation'] == true;
    return InvokeOutcome(
      taskId: json['task_id']?.toString(),
      status: json['status']?.toString(),
      result: json['result'],
      awaitingConfirmation: awaiting,
      confirmation: json['confirmation'] is Map
          ? ConfirmationRequest.fromJson(json['confirmation'], fallbackCapability: fallbackCapability)
          : null,
      error: json['error']?.toString(),
    );
  }
}

/// The parts of GET /tasks/{id} the tester needs.
@immutable
class TaskRunState {
  const TaskRunState({required this.status, this.output, this.error});
  final String status;
  final Object? output;
  final String? error;

  bool get terminal => isTerminalTaskStatus(status);
  bool get succeeded => status == 'completed' && error == null;

  /// Accepts both `{"task": {...}}` and a flat task object.
  factory TaskRunState.fromJson(Object? json) {
    var t = json;
    if (t is Map && t['task'] is Map) t = t['task'];
    if (t is! Map) return const TaskRunState(status: 'unknown');
    final result = t['result'];
    Object? output;
    String? error = t['error']?.toString();
    if (result is Map) {
      output = result.containsKey('output') ? result['output'] : result;
      error ??= result['error']?.toString();
      if (result['success'] == false && error == null) error = 'The action failed.';
    } else if (result != null) {
      output = result;
    }
    return TaskRunState(
      status: (t['status'] ?? 'unknown').toString(),
      output: output,
      error: (error == null || error.isEmpty) ? null : error,
    );
  }
}
