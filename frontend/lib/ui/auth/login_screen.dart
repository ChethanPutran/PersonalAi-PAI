import 'package:flutter/material.dart';

import '../../auth/auth_service.dart';
import '../theme/theme.dart';
import '../widgets/hud/hud.dart';

class LoginScreen extends StatefulWidget {
  final AuthService auth;
  final VoidCallback onAuthenticated;

  const LoginScreen({
    super.key,
    required this.auth,
    required this.onAuthenticated,
  });

  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  final _email = TextEditingController();
  final _password = TextEditingController();

  bool _busy = false;
  bool _isRegistering = false;
  String? _error;

  @override
  void dispose() {
    _email.dispose();
    _password.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    final email = _email.text.trim();
    final password = _password.text;
    if (email.isEmpty || password.isEmpty) {
      setState(() => _error = 'Email and password are required.');
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });

    try {
      if (_isRegistering) {
        await widget.auth.register(email, password);
      } else {
        await widget.auth.login(email, password);
      }
      if (!mounted) return;
      widget.onAuthenticated();
    } catch (e) {
      if (!mounted) return;
      setState(() => _error = e.toString());
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final text = Theme.of(context).textTheme;
    return HudScaffold(
      resizeToAvoidBottomInset: true,
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.symmetric(horizontal: PaiSpacing.xl, vertical: PaiSpacing.lg),
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 420),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Center(
                    child: ArcReactorOrb(
                      state: _busy ? OrbState.thinking : OrbState.idle,
                      size: 148,
                    ),
                  ),
                  const SizedBox(height: PaiSpacing.lg),
                  Text(
                    'PERSONAL AI',
                    textAlign: TextAlign.center,
                    style: text.displaySmall?.copyWith(color: scheme.primary, letterSpacing: 6),
                  ),
                  const SizedBox(height: PaiSpacing.xs),
                  Text(
                    _isRegistering ? 'CREATE YOUR ACCOUNT' : 'SECURE ACCESS',
                    textAlign: TextAlign.center,
                    style: text.labelMedium?.copyWith(color: scheme.onSurfaceVariant, letterSpacing: 4),
                  ),
                  const SizedBox(height: PaiSpacing.xl),
                  HudPanel(
                    blur: true,
                    padding: const EdgeInsets.all(PaiSpacing.xl),
                    title: _isRegistering ? 'Register' : 'Sign in',
                    leadingIcon: Icons.lock_outline,
                    child: Column(
                      mainAxisSize: MainAxisSize.min,
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: [
                        HudTextField(
                          controller: _email,
                          label: 'Email',
                          icon: Icons.alternate_email,
                          keyboardType: TextInputType.emailAddress,
                          textInputAction: TextInputAction.next,
                          autofillHints: const [AutofillHints.email],
                        ),
                        const SizedBox(height: PaiSpacing.md),
                        HudTextField(
                          controller: _password,
                          label: 'Password',
                          icon: Icons.key_outlined,
                          obscureText: true,
                          textInputAction: TextInputAction.done,
                          autofillHints: const [AutofillHints.password],
                          onSubmitted: (_) => _submit(),
                        ),
                        if (_error != null) ...[
                          const SizedBox(height: PaiSpacing.md),
                          Semantics(
                            liveRegion: true,
                            child: Container(
                              padding: const EdgeInsets.all(PaiSpacing.md),
                              decoration: BoxDecoration(
                                color: scheme.error.withValues(alpha: 0.10),
                                borderRadius: BorderRadius.circular(PaiRadii.sm),
                                border: Border.all(color: scheme.error.withValues(alpha: 0.5)),
                              ),
                              child: Row(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  Icon(Icons.error_outline, size: 18, color: scheme.error),
                                  const SizedBox(width: 8),
                                  Expanded(
                                    child: Text(_error!,
                                        style: text.bodySmall?.copyWith(color: scheme.onSurface)),
                                  ),
                                ],
                              ),
                            ),
                          ),
                        ],
                        const SizedBox(height: PaiSpacing.xl),
                        GlowButton(
                          label: _isRegistering ? 'Create account' : 'Sign in',
                          icon: _isRegistering ? Icons.person_add_alt_1 : Icons.login_rounded,
                          expanded: true,
                          loading: _busy,
                          onPressed: _busy ? null : _submit,
                        ),
                      ],
                    ),
                  ),
                  const SizedBox(height: PaiSpacing.md),
                  TextButton(
                    onPressed: _busy
                        ? null
                        : () => setState(() => _isRegistering = !_isRegistering),
                    child: Text(
                      _isRegistering
                          ? 'Already have an account? Sign in'
                          : "Don't have an account? Register",
                      textAlign: TextAlign.center,
                    ),
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}
