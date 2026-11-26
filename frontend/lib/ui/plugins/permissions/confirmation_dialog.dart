import 'package:flutter/material.dart';

import '../../../plugins/permissions/permission_models.dart';
import '../../theme/theme.dart';
import 'permission_widgets.dart';

/// Sends the user's approval to the server. Throw [ConfirmationException] (or anything else)
/// to keep the dialog open and show the message inline.
typedef ConfirmCallback = Future<void> Function(ConfirmationDecision decision);
typedef DenyCallback = Future<void> Function();

/// The ONE approval dialog — used by chat prompts and the plugin tester.
///
/// Returns true if the user approved (and [onConfirm] succeeded), false if they denied.
/// It never approves on its own: the safest action (Deny) has the initial focus, "Allow once"
/// is preselected, and only scopes the server allows are ever offered.
Future<bool> showConfirmationDialog(
  BuildContext context, {
  required ConfirmationRequest request,
  required ConfirmCallback onConfirm,
  required DenyCallback onDeny,
  Set<ConfirmationScope>? limitScopes,
}) async {
  final shown = limitScopes == null ? request : request.restrictedTo(limitScopes);
  final result = await showDialog<bool>(
    context: context,
    barrierDismissible: false,
    builder: (_) => ConfirmationDialog(request: shown, onConfirm: onConfirm, onDeny: onDeny),
  );
  return result == true;
}

class ConfirmationDialog extends StatefulWidget {
  const ConfirmationDialog({
    super.key,
    required this.request,
    required this.onConfirm,
    required this.onDeny,
  });

  final ConfirmationRequest request;
  final ConfirmCallback onConfirm;
  final DenyCallback onDeny;

  @override
  State<ConfirmationDialog> createState() => _ConfirmationDialogState();
}

class _ConfirmationDialogState extends State<ConfirmationDialog> {
  ConfirmationScope _scope = ConfirmationScope.once;
  BindingMode _bindingMode = BindingMode.exact;
  final Set<String> _keys = {};
  int _days = kDefaultAlwaysAllowDays;
  bool _busy = false;
  String? _error;

  ConfirmationRequest get _r => widget.request;

  @override
  void initState() {
    super.initState();
    // Start from the server's suggested binding (usually "exact").
    _bindingMode = _r.defaultBinding.mode;
    _keys.addAll(_r.defaultBinding.keys);
    _keys.addAll(_r.requiredBindingKeys);
    if (_pinned && _bindingMode == BindingMode.any) _bindingMode = BindingMode.keys;
    if (_bindingMode == BindingMode.keys && _r.parameterNames.isEmpty) _bindingMode = BindingMode.exact;
  }

  bool get _showBinding => _scope != ConfirmationScope.once && _r.canBindParams;
  bool get _hasParams => _r.parametersSummary.isNotEmpty;
  bool get _pinned => _r.requiredBindingKeys.isNotEmpty;       // e.g. shell: the command is always pinned

  ParamBinding get _binding => switch (_bindingMode) {
        BindingMode.any => ParamBinding.any,
        BindingMode.exact => ParamBinding.exact,
        BindingMode.keys => ParamBinding(
            mode: BindingMode.keys,
            // keep the parameter order the server showed
            keys: [for (final n in _r.parameterNames) if (_keys.contains(n)) n],
          ),
      };

  bool get _valid => !(_showBinding && _bindingMode == BindingMode.keys && _keys.isEmpty);

  ConfirmationDecision get _decision => switch (_scope) {
        ConfirmationScope.once => const ConfirmationDecision.once(),
        ConfirmationScope.session =>
          ConfirmationDecision.session(binding: _r.canBindParams ? _binding : null),
        ConfirmationScope.always => ConfirmationDecision.always(
            binding: _r.canBindParams ? _binding : null,
            days: _days,
          ),
      };

  String get _allowLabel => switch (_scope) {
        ConfirmationScope.once => 'Allow once',
        ConfirmationScope.session => 'Allow for this session',
        ConfirmationScope.always => 'Always allow',
      };

  Future<void> _confirm() async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await widget.onConfirm(_decision);
      if (mounted) Navigator.of(context).pop(true);
    } on ConfirmationException catch (e) {
      debugPrint('[ConfirmationDialog] server rejected approval: ${e.code} ${e.message}');
      if (mounted) setState(() => _error = e.message);
    } catch (e, st) {
      debugPrint('[ConfirmationDialog] sending approval failed: $e');
      debugPrintStack(stackTrace: st, label: '[ConfirmationDialog] confirm', maxFrames: 6);
      if (mounted) setState(() => _error = 'Could not send your decision: $e');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _deny() async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await widget.onDeny();
      if (mounted) Navigator.of(context).pop(false);
    } catch (e, st) {
      debugPrint('[ConfirmationDialog] deny failed: $e');
      debugPrintStack(stackTrace: st, label: '[ConfirmationDialog] deny', maxFrames: 6);
      if (mounted) setState(() => _error = 'Could not send your decision: $e');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  // ------------------------------------------------------------------ pieces

  Widget _row(String label, Widget value) => Padding(
        padding: const EdgeInsets.only(bottom: PaiSpacing.sm),
        child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          SizedBox(
            width: 84,
            child: Text(label,
                style: Theme.of(context).textTheme.bodySmall?.copyWith(color: context.pai.muted)),
          ),
          Expanded(child: value),
        ]),
      );

  Widget _paramList() {
    if (!_hasParams) {
      return Text('none', style: TextStyle(color: context.pai.muted));
    }
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        for (final p in _r.parametersSummary)
          Padding(
            padding: const EdgeInsets.only(bottom: 2),
            child: SelectableText.rich(TextSpan(children: [
              TextSpan(text: '${p.name}: ', style: PaiText.mono(size: 12, color: context.pai.muted)),
              TextSpan(text: p.valuePreview, style: PaiText.mono(size: 12, color: context.pai.strong)),
            ])),
          ),
      ],
    );
  }

  Widget _riskNote() {
    final elevated = _r.risk.isElevated;
    final onlyOnce = _r.allowsOnly;
    if (!elevated && !onlyOnce) return const SizedBox.shrink();
    final c = elevated ? context.pai.red : context.pai.muted;
    final text = StringBuffer();
    if (_r.risk == ConfirmationRisk.critical) {
      text.write('Critical risk — review the details above carefully before approving. ');
    } else if (_r.risk == ConfirmationRisk.high) {
      text.write('High risk — this can change or expose data on the device. ');
    }
    if (onlyOnce) {
      text.write('For safety this action is approved one time at a time.');
    }
    return Container(
      key: const Key('risk-note'),
      margin: const EdgeInsets.only(bottom: PaiSpacing.md),
      padding: const EdgeInsets.all(PaiSpacing.md),
      decoration: BoxDecoration(
        color: c.withValues(alpha: 0.10),
        border: Border.all(color: c.withValues(alpha: 0.45)),
        borderRadius: BorderRadius.circular(PaiRadii.md),
      ),
      child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Icon(elevated ? Icons.warning_amber_rounded : Icons.info_outline, size: 18, color: c),
        const SizedBox(width: PaiSpacing.sm),
        Expanded(
          child: Text(text.toString().trim(),
              style: Theme.of(context).textTheme.bodySmall?.copyWith(color: context.pai.strong)),
        ),
      ]),
    );
  }

  Widget _scopeChoices() {
    final tiles = <Widget>[
      _ChoiceTile(
        key: const Key('scope-once'),
        selected: _scope == ConfirmationScope.once,
        title: 'Allow once',
        subtitle: 'Run it now. Ask again next time.',
        onTap: _busy ? null : () => setState(() => _scope = ConfirmationScope.once),
      ),
      if (_r.allows(ConfirmationScope.session))
        _ChoiceTile(
          key: const Key('scope-session'),
          selected: _scope == ConfirmationScope.session,
          title: 'Allow for this session',
          subtitle: 'No more questions until you sign out or restart the app.',
          onTap: _busy ? null : () => setState(() => _scope = ConfirmationScope.session),
        ),
      if (_r.allows(ConfirmationScope.always))
        _ChoiceTile(
          key: const Key('scope-always'),
          selected: _scope == ConfirmationScope.always,
          title: 'Always allow…',
          subtitle: 'Remember this. You can revoke it under Permissions.',
          onTap: _busy ? null : () => setState(() => _scope = ConfirmationScope.always),
        ),
    ];
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: tiles);
  }

  Widget _bindingChoices() {
    final text = Theme.of(context).textTheme;
    return Column(
      key: const Key('binding-section'),
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const SizedBox(height: PaiSpacing.md),
        Text('Applies to', style: text.labelLarge),
        const SizedBox(height: PaiSpacing.xs),
        _ChoiceTile(
          key: const Key('binding-exact'),
          selected: _bindingMode == BindingMode.exact,
          title: _hasParams ? 'Only with these exact parameters' : 'Only with no parameters',
          onTap: _busy ? null : () => setState(() => _bindingMode = BindingMode.exact),
        ),
        if (_hasParams)
          _ChoiceTile(
            key: const Key('binding-keys'),
            selected: _bindingMode == BindingMode.keys,
            title: 'Only these parameters…',
            subtitle: 'Any values for the ones you pick; the rest must match.',
            onTap: _busy ? null : () => setState(() => _bindingMode = BindingMode.keys),
          ),
        if (_bindingMode == BindingMode.keys && _hasParams)
          Padding(
            padding: const EdgeInsets.only(left: PaiSpacing.xl),
            child: Column(children: [
              for (final name in _r.parameterNames)
                CheckboxListTile(
                  key: Key('key-$name'),
                  dense: true,
                  contentPadding: EdgeInsets.zero,
                  controlAffinity: ListTileControlAffinity.leading,
                  title: Text(name, style: PaiText.mono(size: 12, color: context.pai.strong)),
                  value: _keys.contains(name),
                  onChanged: _busy || _r.requiredBindingKeys.contains(name)
                      ? null
                      : (v) => setState(() => v == true ? _keys.add(name) : _keys.remove(name)),
                ),
              if (_keys.isEmpty)
                Padding(
                  padding: const EdgeInsets.only(bottom: PaiSpacing.xs),
                  child: Align(
                    alignment: Alignment.centerLeft,
                    child: Text('Pick at least one parameter.',
                        style: text.bodySmall?.copyWith(color: context.pai.amber)),
                  ),
                ),
            ]),
          ),
        if (!_pinned)
        _ChoiceTile(
          key: const Key('binding-any'),
          selected: _bindingMode == BindingMode.any,
          title: 'Any parameters',
          onTap: _busy ? null : () => setState(() => _bindingMode = BindingMode.any),
        ),
        if (!_pinned && _bindingMode == BindingMode.any)
          Padding(
            key: const Key('binding-any-warning'),
            padding: const EdgeInsets.only(left: PaiSpacing.xl, bottom: PaiSpacing.xs),
            child: Text(
              'Broader: this will apply to this action with any parameters, not just the ones '
              'shown above.',
              style: text.bodySmall?.copyWith(color: context.pai.amber),
            ),
          ),
      ],
    );
  }

  Widget _expiryChoice() {
    final text = Theme.of(context).textTheme;
    final until = PaiClock.now().add(Duration(days: _days));
    return Column(
      key: const Key('expiry-section'),
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const SizedBox(height: PaiSpacing.md),
        Text('Keep it for', style: text.labelLarge),
        const SizedBox(height: PaiSpacing.xs),
        SegmentedButton<int>(
          showSelectedIcon: false,
          style: const ButtonStyle(
            minimumSize: WidgetStatePropertyAll(Size(0, 48)),
            padding: WidgetStatePropertyAll(EdgeInsets.symmetric(horizontal: 6)),
          ),
          segments: [
            for (final d in kAlwaysAllowDays) ButtonSegment<int>(value: d, label: Text('$d days', maxLines: 1, softWrap: false)),
          ],
          selected: {_days},
          onSelectionChanged: _busy ? null : (s) => setState(() => _days = s.first),
        ),
        const SizedBox(height: PaiSpacing.xs),
        Text('Until ${fullDate(until)}', style: text.bodySmall?.copyWith(color: context.pai.muted)),
      ],
    );
  }

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final device = _r.deviceName ?? _r.deviceId ?? 'this device';
    return AlertDialog(
      title: Text('Approve ${_r.capability}?', key: const Key('confirm-title')),
      content: SizedBox(
        width: 440,
        child: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              if (_r.reason != null)
                Padding(
                  padding: const EdgeInsets.only(bottom: PaiSpacing.md),
                  child: Text(_r.reason!, style: text.bodyMedium),
                ),
              _row('Action', Text(_r.capability, style: PaiText.mono(size: 13, color: context.pai.strong))),
              _row('Device', Text(device)),
              _row('Risk', Align(alignment: Alignment.centerLeft, child: RiskBadge(_r.risk))),
              _row('Parameters', _paramList()),
              const SizedBox(height: PaiSpacing.xs),
              _riskNote(),
              Text('How long?', style: text.labelLarge),
              const SizedBox(height: PaiSpacing.xs),
              _scopeChoices(),
              if (_showBinding) _bindingChoices(),
              if (_scope == ConfirmationScope.always) _expiryChoice(),
              if (_error != null)
                Container(
                  key: const Key('confirm-error'),
                  margin: const EdgeInsets.only(top: PaiSpacing.md),
                  padding: const EdgeInsets.all(PaiSpacing.md),
                  decoration: BoxDecoration(
                    color: context.pai.red.withValues(alpha: 0.10),
                    border: Border.all(color: context.pai.red.withValues(alpha: 0.45)),
                    borderRadius: BorderRadius.circular(PaiRadii.md),
                  ),
                  child: Text(_error!, style: TextStyle(color: context.pai.red)),
                ),
            ],
          ),
        ),
      ),
      actions: [
        TextButton(
          key: const Key('confirm-deny'),
          autofocus: true, // the safest action holds the initial focus
          onPressed: _busy ? null : _deny,
          child: const Text('Deny'),
        ),
        FilledButton(
          key: const Key('confirm-allow'),
          onPressed: (_busy || !_valid) ? null : _confirm,
          child: _busy
              ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))
              : Text(_allowLabel),
        ),
      ],
    );
  }
}

/// One radio-style row: at least 48 dp tall, announced as a mutually exclusive choice.
class _ChoiceTile extends StatelessWidget {
  const _ChoiceTile({
    super.key,
    required this.selected,
    required this.title,
    this.subtitle,
    required this.onTap,
  });

  final bool selected;
  final String title;
  final String? subtitle;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final accent = context.pai.cyan;
    return Semantics(
      inMutuallyExclusiveGroup: true,
      selected: selected,
      button: true,
      enabled: onTap != null,
      label: title,
      excludeSemantics: true,
      onTap: onTap,
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(PaiRadii.md),
        child: ConstrainedBox(
          constraints: const BoxConstraints(minHeight: 48),
          child: Padding(
            padding: const EdgeInsets.symmetric(vertical: PaiSpacing.xs, horizontal: PaiSpacing.xs),
            child: Row(crossAxisAlignment: CrossAxisAlignment.center, children: [
              Icon(selected ? Icons.radio_button_checked : Icons.radio_button_unchecked,
                  size: 20, color: selected ? accent : context.pai.muted),
              const SizedBox(width: PaiSpacing.sm),
              Expanded(
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Text(title,
                      style: text.bodyMedium?.copyWith(
                          color: context.pai.strong,
                          fontWeight: selected ? FontWeight.w600 : FontWeight.w400)),
                  if (subtitle != null)
                    Text(subtitle!, style: text.bodySmall?.copyWith(color: context.pai.muted)),
                ]),
              ),
            ]),
          ),
        ),
      ),
    );
  }
}
