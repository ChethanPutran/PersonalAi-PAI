import 'package:flutter/material.dart';

import 'hud_background.dart';

/// Scaffold with the animated HUD backdrop behind it. Drop-in replacement for [Scaffold].
class HudScaffold extends StatelessWidget {
  const HudScaffold({
    super.key,
    this.appBar,
    this.body,
    this.bottomNavigationBar,
    this.floatingActionButton,
    this.floatingActionButtonLocation,
    this.drawer,
    this.resizeToAvoidBottomInset,
    this.animateBackground = true,
    this.extendBodyBehindAppBar = false,
  });

  final PreferredSizeWidget? appBar;
  final Widget? body;
  final Widget? bottomNavigationBar;
  final Widget? floatingActionButton;
  final FloatingActionButtonLocation? floatingActionButtonLocation;
  final Widget? drawer;
  final bool? resizeToAvoidBottomInset;
  final bool animateBackground;
  final bool extendBodyBehindAppBar;

  @override
  Widget build(BuildContext context) {
    final scaffold = Scaffold(
      backgroundColor: Colors.transparent,
      appBar: appBar,
      body: body,
      bottomNavigationBar: bottomNavigationBar,
      floatingActionButton: floatingActionButton,
      floatingActionButtonLocation: floatingActionButtonLocation,
      drawer: drawer,
      resizeToAvoidBottomInset: resizeToAvoidBottomInset,
      extendBodyBehindAppBar: extendBodyBehindAppBar,
    );
    // Inside the app shell the backdrop already exists: don't paint (and animate) another.
    if (HudBackdropScope.isProvided(context)) return scaffold;
    return HudBackground(animate: animateBackground, child: scaffold);
  }
}

/// AppBar with a glowing hairline underneath. Title is rendered in the display font.
class HudAppBar extends StatelessWidget implements PreferredSizeWidget {
  const HudAppBar({
    super.key,
    this.title,
    this.titleWidget,
    this.actions,
    this.leading,
    this.bottom,
    this.automaticallyImplyLeading = true,
  });

  final String? title;
  final Widget? titleWidget;
  final List<Widget>? actions;
  final Widget? leading;
  final PreferredSizeWidget? bottom;
  final bool automaticallyImplyLeading;

  @override
  Size get preferredSize => Size.fromHeight(kToolbarHeight + 1 + (bottom?.preferredSize.height ?? 0));

  @override
  Widget build(BuildContext context) {
    final primary = Theme.of(context).colorScheme.primary;
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        AppBar(
          automaticallyImplyLeading: automaticallyImplyLeading,
          leading: leading,
          title: titleWidget ?? (title == null ? null : Text(title!.toUpperCase())),
          actions: actions,
          bottom: bottom,
        ),
        Container(
          height: 1,
          decoration: BoxDecoration(
            gradient: LinearGradient(colors: [
              primary.withValues(alpha: 0),
              primary.withValues(alpha: 0.55),
              primary.withValues(alpha: 0),
            ]),
          ),
        ),
      ],
    );
  }
}
