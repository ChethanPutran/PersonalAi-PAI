import 'package:flutter/material.dart';

import '../theme/theme.dart';
import '../widgets/hud/hud.dart';

class PaiDestination {
  const PaiDestination({required this.icon, required this.selectedIcon, required this.label});
  final IconData icon;
  final IconData selectedIcon;
  final String label;
}

/// Wordmark: a tiny reactor glyph + "PAI" in the display font.
class PaiBrandMark extends StatelessWidget {
  const PaiBrandMark({super.key, this.compact = false});
  final bool compact;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Semantics(
      label: 'PAI',
      excludeSemantics: true,
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          SizedBox(
            width: compact ? 26 : 32,
            height: compact ? 26 : 32,
            child: const ArcReactorOrb(state: OrbState.idle, size: 32, animate: false),
          ),
          const SizedBox(width: 10),
          Text('PAI',
              style: TextStyle(
                  fontFamily: PaiFonts.display, fontSize: compact ? 16 : 19, fontWeight: FontWeight.w800,
                  letterSpacing: 4, color: scheme.primary)),
        ],
      ),
    );
  }
}

/// App frame: HUD backdrop + adaptive navigation.
///  * width < 720   : top bar + bottom NavigationBar
///  * width >= 720  : NavigationRail (brand on top, sign-out at the bottom) + content
class PaiShell extends StatelessWidget {
  const PaiShell({
    super.key,
    required this.destinations,
    required this.index,
    required this.onSelect,
    required this.body,
    required this.onSignOut,
    this.animateBackground = true,
  });

  final List<PaiDestination> destinations;
  final int index;
  final ValueChanged<int> onSelect;
  final Widget body;
  final VoidCallback onSignOut;
  final bool animateBackground;

  @override
  Widget build(BuildContext context) {
    final wide = PaiBreakpoints.isWide(context);
    // ONE backdrop for all tabs; the screens keep their own title bars (each tab has its own
    // actions), so the shell adds only navigation. Sign-out lives in the rail on wide screens
    // and on the Profile tab on phones.
    if (!wide) {
      return HudBackground(
        animate: animateBackground,
        child: HudBackdropScope(
          child: Scaffold(
            backgroundColor: Colors.transparent,
            body: SafeArea(bottom: false, child: body),
            bottomNavigationBar: DecoratedBox(
              decoration: BoxDecoration(border: Border(top: BorderSide(color: PaiColors.border))),
              child: NavigationBar(
                selectedIndex: index,
                onDestinationSelected: onSelect,
                labelBehavior: NavigationDestinationLabelBehavior.alwaysShow,
                destinations: [
                  for (final d in destinations)
                    NavigationDestination(icon: Icon(d.icon), selectedIcon: Icon(d.selectedIcon), label: d.label),
                ],
              ),
            ),
          ),
        ),
      );
    }

    return HudBackground(
      animate: animateBackground,
      child: HudBackdropScope(
        child: Scaffold(
          backgroundColor: Colors.transparent,
          body: SafeArea(
            child: Row(
              children: [
                DecoratedBox(
                  decoration: BoxDecoration(border: Border(right: BorderSide(color: PaiColors.border))),
                  child: NavigationRail(
                    selectedIndex: index,
                    onDestinationSelected: onSelect,
                    minWidth: 88,
                    leading: const Padding(
                      padding: EdgeInsets.fromLTRB(0, 16, 0, 24),
                      child: SizedBox(width: 56, height: 56, child: ArcReactorOrb(state: OrbState.idle, size: 56, animate: false)),
                    ),
                    trailing: Expanded(
                      child: Align(
                        alignment: Alignment.bottomCenter,
                        child: Padding(
                          padding: const EdgeInsets.only(bottom: 16),
                          child: HudIconButton(icon: Icons.logout_rounded, tooltip: 'Sign out', onPressed: onSignOut),
                        ),
                      ),
                    ),
                    destinations: [
                      for (final d in destinations)
                        NavigationRailDestination(
                          icon: Icon(d.icon),
                          selectedIcon: Icon(d.selectedIcon),
                          label: Text(d.label.toUpperCase()),
                          padding: const EdgeInsets.symmetric(vertical: 4),
                        ),
                    ],
                  ),
                ),
                Expanded(child: body),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
