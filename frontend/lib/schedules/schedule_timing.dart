import '../models/schedule.dart';

/// The simple repeat choices of the editor. Internally everything ends up as one of the
/// backend's three kinds: `one_shot`, `cron` or `interval`.
enum RepeatChoice {
  once,
  hourly,
  daily,
  weekly,
  monthly,
  yearly,
  customCron,

  /// An existing interval other than hourly, kept as-is when editing (not offered for new ones).
  keepInterval,
}

/// A repeat choice plus the values that go with it.
class Timing {
  final RepeatChoice repeat;

  /// `once`: the moment. Presets: its time of day (and weekday/day/month) define the cron.
  final DateTime fireAt;
  final String cron; // customCron only
  final int? intervalSeconds; // keepInterval only

  const Timing({
    required this.repeat,
    required this.fireAt,
    this.cron = '0 9 * * *',
    this.intervalSeconds,
  });

  Timing copyWith({RepeatChoice? repeat, DateTime? fireAt, String? cron}) => Timing(
        repeat: repeat ?? this.repeat,
        fireAt: fireAt ?? this.fireAt,
        cron: cron ?? this.cron,
        intervalSeconds: intervalSeconds,
      );
}

/// What the backend receives for a [Timing].
class EncodedTiming {
  final String kind; // one_shot | cron | interval
  final String? cron;
  final int? intervalSeconds;
  final DateTime? fireAt;
  const EncodedTiming(this.kind, {this.cron, this.intervalSeconds, this.fireAt});
}

EncodedTiming encodeTiming(Timing t) {
  final d = t.fireAt;
  switch (t.repeat) {
    case RepeatChoice.once:
      return EncodedTiming('one_shot', fireAt: d);
    case RepeatChoice.hourly:
      return const EncodedTiming('interval', intervalSeconds: 3600);
    case RepeatChoice.keepInterval:
      return EncodedTiming('interval', intervalSeconds: t.intervalSeconds ?? 3600);
    case RepeatChoice.daily:
      return EncodedTiming('cron', cron: '${d.minute} ${d.hour} * * *');
    case RepeatChoice.weekly:
      // day-of-week 0=Sun … 6=Sat
      return EncodedTiming('cron', cron: '${d.minute} ${d.hour} * * ${d.weekday % 7}');
    case RepeatChoice.monthly:
      return EncodedTiming('cron', cron: '${d.minute} ${d.hour} ${d.day} * *');
    case RepeatChoice.yearly:
      return EncodedTiming('cron', cron: '${d.minute} ${d.hour} ${d.day} ${d.month} *');
    case RepeatChoice.customCron:
      return EncodedTiming('cron', cron: t.cron.trim());
  }
}

/// Reverse of [encodeTiming], for editing an existing schedule. Cron expressions that match
/// one of the presets come back as that preset; anything else is a custom cron.
Timing decodeTiming(Schedule s, {DateTime? now}) {
  final base = now ?? DateTime.now();
  switch (s.kind) {
    case 'one_shot':
      return Timing(repeat: RepeatChoice.once, fireAt: s.fireAt ?? base.add(const Duration(hours: 1)));
    case 'interval':
      final secs = s.intervalSeconds ?? 3600;
      return Timing(
        repeat: secs == 3600 ? RepeatChoice.hourly : RepeatChoice.keepInterval,
        fireAt: base.add(const Duration(hours: 1)),
        intervalSeconds: secs,
      );
  }
  final cron = (s.cron ?? '').trim();
  DateTime at(int h, int m, {int? day, int? month, int? weekday}) {
    var d = DateTime(base.year, month ?? base.month, day ?? base.day, h, m);
    if (weekday != null) {
      // weekday: 0=Sun … 6=Sat; pick the next date with that weekday
      while (d.weekday % 7 != weekday) {
        d = d.add(const Duration(days: 1));
      }
    }
    return d;
  }

  RegExpMatch? m;
  if ((m = RegExp(r'^(\d{1,2}) (\d{1,2}) \* \* \*$').firstMatch(cron)) != null) {
    return Timing(repeat: RepeatChoice.daily, fireAt: at(int.parse(m!.group(2)!), int.parse(m.group(1)!)));
  }
  if ((m = RegExp(r'^(\d{1,2}) (\d{1,2}) \* \* ([0-6])$').firstMatch(cron)) != null) {
    return Timing(
        repeat: RepeatChoice.weekly,
        fireAt: at(int.parse(m!.group(2)!), int.parse(m.group(1)!), weekday: int.parse(m.group(3)!)));
  }
  if ((m = RegExp(r'^(\d{1,2}) (\d{1,2}) (\d{1,2}) \* \*$').firstMatch(cron)) != null) {
    return Timing(
        repeat: RepeatChoice.monthly,
        fireAt: at(int.parse(m!.group(2)!), int.parse(m.group(1)!), day: int.parse(m.group(3)!)));
  }
  if ((m = RegExp(r'^(\d{1,2}) (\d{1,2}) (\d{1,2}) (\d{1,2}) \*$').firstMatch(cron)) != null) {
    return Timing(
        repeat: RepeatChoice.yearly,
        fireAt: at(int.parse(m!.group(2)!), int.parse(m.group(1)!),
            day: int.parse(m.group(3)!), month: int.parse(m.group(4)!)));
  }
  return Timing(
      repeat: RepeatChoice.customCron,
      fireAt: base.add(const Duration(hours: 1)),
      cron: cron.isEmpty ? '0 9 * * *' : cron);
}

String _hhmm(DateTime d) =>
    '${d.hour.toString().padLeft(2, '0')}:${d.minute.toString().padLeft(2, '0')}';

const _months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const _weekdays = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];

/// "daily at 09:00", "once on Nov 24, 09:00", ... — reads naturally after "Runs … ".
String describeTiming(Timing t) {
  final d = t.fireAt;
  switch (t.repeat) {
    case RepeatChoice.once:
      return 'once on ${_months[d.month - 1]} ${d.day}, ${_hhmm(d)}';
    case RepeatChoice.hourly:
      return 'every hour';
    case RepeatChoice.keepInterval:
      return 'every ${_everyLabel(t.intervalSeconds ?? 3600)}';
    case RepeatChoice.daily:
      return 'daily at ${_hhmm(d)}';
    case RepeatChoice.weekly:
      return 'every ${_weekdays[d.weekday - 1]} at ${_hhmm(d)}';
    case RepeatChoice.monthly:
      return 'monthly on day ${d.day} at ${_hhmm(d)}';
    case RepeatChoice.yearly:
      return 'every year on ${_months[d.month - 1]} ${d.day} at ${_hhmm(d)}';
    case RepeatChoice.customCron:
      return 'on cron ${t.cron.trim().isEmpty ? '—' : t.cron.trim()}';
  }
}

String _everyLabel(int s) {
  if (s % 86400 == 0) return '${s ~/ 86400} day${s ~/ 86400 == 1 ? '' : 's'}';
  if (s % 3600 == 0) return '${s ~/ 3600} hours';
  if (s % 60 == 0) return '${s ~/ 60} minutes';
  return '$s seconds';
}
