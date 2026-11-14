class ChatStatus {
  final String sessionId;
  final String requestId;
  final String stage;     // planning | planned | executing | replying | done | failed
  final String message;
  final double? progress;
  final Map<String, dynamic> details;
  final DateTime timestamp;

  const ChatStatus({
    required this.sessionId,
    required this.requestId,
    required this.stage,
    required this.message,
    required this.timestamp,
    this.progress,
    this.details = const {},
  });

  factory ChatStatus.fromJson(Map<String, dynamic> j) {
    return ChatStatus(
      sessionId: j['session_id']?.toString() ?? '',
      requestId: j['request_id']?.toString() ?? '',
      stage: j['stage']?.toString() ?? '',
      message: j['message']?.toString() ?? '',
      progress: (j['progress'] as num?)?.toDouble(),
      details: j['details'] is Map
          ? Map<String, dynamic>.from(j['details'] as Map)
          : const {},
      timestamp: DateTime.tryParse(j['timestamp']?.toString() ?? '')
              ?.toLocal() ??
          DateTime.now(),
    );
  }

  bool get isTerminal => stage == 'done' || stage == 'failed' || stage == 'completed';
  bool get isFailure => stage == 'failed';
}