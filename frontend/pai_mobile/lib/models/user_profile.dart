class UserProfile {
  final String id;
  final String email;
  final bool isAdmin;
  final DateTime? createdAt;

  const UserProfile({
    required this.id,
    required this.email,
    required this.isAdmin,
    this.createdAt,
  });

  factory UserProfile.fromJson(Map<String, dynamic> json) {
    final createdAt = json['created_at']?.toString();
    return UserProfile(
      id: json['id']?.toString() ?? '',
      email: json['email']?.toString() ?? '',
      isAdmin: json['is_admin'] == true,
      createdAt: createdAt != null ? DateTime.tryParse(createdAt) : null,
    );
  }

  String get initials {
    if (email.isEmpty) return '?';
    final local = email.split('@').first;
    if (local.isEmpty) return '?';
    return local[0].toUpperCase();
  }
}