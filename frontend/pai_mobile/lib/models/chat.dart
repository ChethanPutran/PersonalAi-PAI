class ChatResponse {
  final String message;
  final String status;
  final String sessionId;
  final List<Map<String, dynamic>> tasks;

  ChatResponse({
    required this.message,
    required this.status,
    required this.sessionId,
    required this.tasks,
  });

  factory ChatResponse.fromJson(Map<String, dynamic> j) => ChatResponse(
    message: j['message']?.toString() ?? '',
    status: j['status']?.toString() ?? 'unknown',
    sessionId: j['session_id']?.toString() ?? '',
    tasks: ((j['tasks'] as List?) ?? []).cast<Map<String, dynamic>>(),
  );
}

class ChatSessionSummary {
  final String id;
  final String title;
  final int messageCount;
  final DateTime? lastMessageAt;
  ChatSessionSummary({required this.id, required this.title,
    required this.messageCount, this.lastMessageAt});

  factory ChatSessionSummary.fromJson(Map<String, dynamic> j) => ChatSessionSummary(
    id: j['id'] as String,
    title: j['title'] as String? ?? 'New chat',
    messageCount: (j['message_count'] as num?)?.toInt() ?? 0,
    lastMessageAt: j['last_message_at'] != null
        ? DateTime.parse(j['last_message_at'] as String) : null,
  );
}

class ChatMessage {
  final String id;
  final String role;  // 'user' | 'assistant'
  final String content;
  final DateTime createdAt;
  ChatMessage({required this.id, required this.role,
    required this.content, required this.createdAt});
  factory ChatMessage.fromJson(Map<String, dynamic> j) => ChatMessage(
    id: j['id'] as String,
    role: j['role'] as String,
    content: j['content'] as String,
    createdAt: DateTime.parse(j['created_at'] as String),
  );
}

class ChatSessionDetail {
  final ChatSessionSummary session;
  final List<ChatMessage> messages;
  ChatSessionDetail({required this.session, required this.messages});
  factory ChatSessionDetail.fromJson(Map<String, dynamic> j) => ChatSessionDetail(
    session: ChatSessionSummary.fromJson(j['session']),
    messages: (j['messages'] as List)
        .map((e) => ChatMessage.fromJson(e)).toList(),
  );
}