class AIMessage {
  final String type;
  final String? goal;
  final Map<String, dynamic>? context;
  final Map<String, dynamic>? result;

  AIMessage({this.type = 'goal', this.goal, this.context, this.result});

  Map<String, dynamic> toJson() => {
        'type': type,
        if (goal != null) 'goal': goal,
        if (context != null) 'context': context,
        if (result != null) 'result': result,
      };

  factory AIMessage.fromJson(Map<String, dynamic> json) => AIMessage(
        type: json['type'] ?? 'unknown',
        goal: json['goal'],
        context: json['context'],
        result: (json['result'] ?? json['data']) as Map<String, dynamic>?,
      );
}