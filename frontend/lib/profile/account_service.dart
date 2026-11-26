// lib/profile/account_service.dart
//
// The signed-in user's own account, beyond sign-in: who they are, what they like, and what this
// device is called. The assistant uses the personalization to answer in a way that fits the person.
//
//   GET/PATCH /account/profile          {display_name, timezone (IANA), locale}
//   GET/PUT   /account/personalization  {preferred_name, about, occupation, likes, dislikes,
//                                        interests, hobbies, tone, detail}
//   PATCH     /devices/{id}             {name}                     (owner only)

import '../core/api/api_client.dart';

/// Limits the server enforces too (so the form can say so before sending).
class AccountLimits {
  static const deviceName = 80;
  static const displayName = 80;
  static const preferredName = 60;
  static const occupation = 80;
  static const about = 1000;
  static const chip = 60;
  static const chips = 30;
}

const personalizationTones = ['friendly', 'casual', 'formal', 'concise'];
const personalizationDetails = ['brief', 'balanced', 'detailed'];

class AccountProfile {
  final String id;
  final String email;
  final String displayName;
  final String timezone;
  final String locale;
  final DateTime? createdAt;

  const AccountProfile({
    required this.id,
    required this.email,
    this.displayName = '',
    this.timezone = '',
    this.locale = '',
    this.createdAt,
  });

  factory AccountProfile.fromJson(Map<String, dynamic> j) => AccountProfile(
        id: j['id']?.toString() ?? '',
        email: j['email']?.toString() ?? '',
        displayName: j['display_name']?.toString() ?? '',
        timezone: j['timezone']?.toString() ?? '',
        locale: j['locale']?.toString() ?? '',
        createdAt: DateTime.tryParse(j['created_at']?.toString() ?? ''),
      );
}

class Personalization {
  final String preferredName;
  final String about;
  final String occupation;
  final List<String> likes;
  final List<String> dislikes;
  final List<String> interests;
  final List<String> hobbies;
  final String tone;
  final String detail;

  const Personalization({
    this.preferredName = '',
    this.about = '',
    this.occupation = '',
    this.likes = const [],
    this.dislikes = const [],
    this.interests = const [],
    this.hobbies = const [],
    this.tone = 'friendly',
    this.detail = 'balanced',
  });

  factory Personalization.fromJson(Map<String, dynamic> j) {
    List<String> list(Object? v) => v is List ? [for (final e in v) e.toString()] : const [];
    String pick(Object? v, List<String> allowed, String fallback) =>
        allowed.contains(v?.toString()) ? v.toString() : fallback;
    return Personalization(
      preferredName: j['preferred_name']?.toString() ?? '',
      about: j['about']?.toString() ?? '',
      occupation: j['occupation']?.toString() ?? '',
      likes: list(j['likes']),
      dislikes: list(j['dislikes']),
      interests: list(j['interests']),
      hobbies: list(j['hobbies']),
      tone: pick(j['tone'], personalizationTones, 'friendly'),
      detail: pick(j['detail'], personalizationDetails, 'balanced'),
    );
  }

  Map<String, dynamic> toJson() => {
        'preferred_name': preferredName,
        'about': about,
        'occupation': occupation,
        'likes': likes,
        'dislikes': dislikes,
        'interests': interests,
        'hobbies': hobbies,
        'tone': tone,
        'detail': detail,
      };

  Personalization copyWith({
    String? preferredName,
    String? about,
    String? occupation,
    List<String>? likes,
    List<String>? dislikes,
    List<String>? interests,
    List<String>? hobbies,
    String? tone,
    String? detail,
  }) =>
      Personalization(
        preferredName: preferredName ?? this.preferredName,
        about: about ?? this.about,
        occupation: occupation ?? this.occupation,
        likes: likes ?? this.likes,
        dislikes: dislikes ?? this.dislikes,
        interests: interests ?? this.interests,
        hobbies: hobbies ?? this.hobbies,
        tone: tone ?? this.tone,
        detail: detail ?? this.detail,
      );

  /// Nothing personal filled in (the defaults for tone/detail do not count).
  bool get isBlank =>
      preferredName.isEmpty &&
      about.isEmpty &&
      occupation.isEmpty &&
      likes.isEmpty &&
      dislikes.isEmpty &&
      interests.isEmpty &&
      hobbies.isEmpty;

  @override
  bool operator ==(Object other) =>
      other is Personalization && _same(toJson(), other.toJson());

  @override
  int get hashCode => Object.hashAll([preferredName, about, occupation, tone, detail, ...likes, ...dislikes, ...interests, ...hobbies]);

  static bool _same(Map<String, dynamic> a, Map<String, dynamic> b) {
    for (final k in a.keys) {
      final x = a[k], y = b[k];
      if (x is List) {
        if (y is! List || x.length != y.length) return false;
        for (var i = 0; i < x.length; i++) {
          if (x[i] != y[i]) return false;
        }
      } else if (x != y) {
        return false;
      }
    }
    return true;
  }
}

/// A user-readable reason for a failed account call.
String accountErrorMessage(Object e) {
  if (e is ApiException) {
    final b = e.body;
    final d = b is Map ? b['detail'] : null;
    if (d is String && d.isNotEmpty) return d;
    if (d is Map && d['message'] != null) return d['message'].toString();
    if (d is List && d.isNotEmpty) {
      final msgs = [for (final x in d) if (x is Map && x['msg'] != null) x['msg'].toString().replaceFirst('Value error, ', '')];
      if (msgs.isNotEmpty) return msgs.join('. ');
    }
    return 'The server said no (${e.statusCode}). Try again.';
  }
  return 'Check your connection and try again.';
}

class AccountService {
  AccountService({required this.api});
  final ApiClient api;

  Map<String, dynamic> _map(dynamic v) {
    if (v is Map) return Map<String, dynamic>.from(v);
    throw ApiException(500, 'Unexpected response: $v');
  }

  Future<AccountProfile> getProfile() async => AccountProfile.fromJson(_map(await api.get('/account/profile')));

  /// Send only what changed; an empty string clears a field.
  Future<AccountProfile> updateProfile({String? displayName, String? timezone, String? locale}) async {
    final body = <String, dynamic>{
      if (displayName != null) 'display_name': displayName.trim(),
      if (timezone != null) 'timezone': timezone.trim(),
      if (locale != null) 'locale': locale.trim(),
    };
    return AccountProfile.fromJson(_map(await api.patch('/account/profile', body)));
  }

  Future<Personalization> getPersonalization() async =>
      Personalization.fromJson(_map(await api.get('/account/personalization')));

  Future<Personalization> savePersonalization(Personalization p) async =>
      Personalization.fromJson(_map(await api.put('/account/personalization', p.toJson())));

  /// Returns the name the server stored.
  Future<String> renameDevice(String deviceId, String name) async {
    final res = _map(await api.patch('/devices/$deviceId', {'name': name.trim()}));
    final device = res['device'] is Map ? Map<String, dynamic>.from(res['device'] as Map) : res;
    return device['name']?.toString() ?? name.trim();
  }
}
