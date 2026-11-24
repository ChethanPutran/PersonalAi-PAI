import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

/// Themed text field: HUD label above, filled glass field, cyan focus ring (all from the theme).
class HudTextField extends StatelessWidget {
  const HudTextField({
    super.key,
    this.controller,
    this.label,
    this.hint,
    this.icon,
    this.obscureText = false,
    this.keyboardType,
    this.textInputAction,
    this.onSubmitted,
    this.onChanged,
    this.errorText,
    this.autofillHints,
    this.maxLines = 1,
    this.enabled = true,
    this.suffix,
    this.focusNode,
    this.inputFormatters,
    this.autofocus = false,
  });

  final TextEditingController? controller;
  final String? label;
  final String? hint;
  final IconData? icon;
  final bool obscureText;
  final TextInputType? keyboardType;
  final TextInputAction? textInputAction;
  final ValueChanged<String>? onSubmitted;
  final ValueChanged<String>? onChanged;
  final String? errorText;
  final Iterable<String>? autofillHints;
  final int? maxLines;
  final bool enabled;
  final Widget? suffix;
  final FocusNode? focusNode;
  final List<TextInputFormatter>? inputFormatters;
  final bool autofocus;

  @override
  Widget build(BuildContext context) {
    return TextField(
      controller: controller,
      focusNode: focusNode,
      obscureText: obscureText,
      keyboardType: keyboardType,
      textInputAction: textInputAction,
      onSubmitted: onSubmitted,
      onChanged: onChanged,
      autofillHints: autofillHints,
      maxLines: obscureText ? 1 : maxLines,
      enabled: enabled,
      autofocus: autofocus,
      inputFormatters: inputFormatters,
      style: Theme.of(context).textTheme.bodyLarge,
      decoration: InputDecoration(
        labelText: label,
        hintText: hint,
        errorText: errorText,
        prefixIcon: icon == null ? null : Icon(icon, size: 20),
        suffixIcon: suffix,
      ),
    );
  }
}
