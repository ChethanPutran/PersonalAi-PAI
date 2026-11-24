import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../../theme/theme.dart';

/// Tells descendant [HudScaffold]s that a backdrop is already painted above them (the app
/// shell draws ONE for all tabs), so they must not stack a second animated one.
class HudBackdropScope extends InheritedWidget {
  const HudBackdropScope({super.key, required super.child});

  static bool isProvided(BuildContext context) =>
      context.getInheritedWidgetOfExactType<HudBackdropScope>() != null;

  @override
  bool updateShouldNotify(HudBackdropScope old) => false;
}

/// Animated HUD backdrop: deep gradient, faint perspective-free grid, one slow scanline and a
/// vignette. One [AnimationController], one [RepaintBoundary]; it stops (static frame) when the
/// platform asks for reduced motion or [animate] is false.
class HudBackground extends StatefulWidget {
  const HudBackground({super.key, this.animate = true, this.child});

  /// Tests only: paint a flat colour instead of the gradient/grid/vignette. Gradients are
  /// dithered, which makes golden PNGs several times larger; most goldens are flat, a couple
  /// keep the full backdrop.
  @visibleForTesting
  static bool debugFlat = false;

  final bool animate;
  final Widget? child;

  @override
  State<HudBackground> createState() => _HudBackgroundState();
}

class _HudBackgroundState extends State<HudBackground> with SingleTickerProviderStateMixin {
  late final AnimationController _c =
      AnimationController(vsync: this, duration: PaiDurations.ambient);

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    final run = widget.animate && !MediaQuery.disableAnimationsOf(context);
    if (run && !_c.isAnimating) {
      _c.repeat();
    } else if (!run && _c.isAnimating) {
      _c.stop();
    }
  }

  @override
  void didUpdateWidget(HudBackground old) {
    super.didUpdateWidget(old);
    didChangeDependencies();
  }

  @override
  void dispose() {
    _c.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final dark = Theme.of(context).brightness == Brightness.dark;
    if (HudBackground.debugFlat) {
      return ColoredBox(
          color: dark ? PaiColors.bg1 : Theme.of(context).scaffoldBackgroundColor, child: widget.child);
    }
    if (!dark) return ColoredBox(color: Theme.of(context).scaffoldBackgroundColor, child: widget.child);
    return Stack(
      fit: StackFit.expand,
      children: [
        RepaintBoundary(
          child: AnimatedBuilder(
            animation: _c,
            builder: (_, _) => CustomPaint(painter: _BackgroundPainter(_c.value)),
          ),
        ),
        if (widget.child != null) widget.child!,
      ],
    );
  }
}

class _BackgroundPainter extends CustomPainter {
  _BackgroundPainter(this.t);
  final double t;

  @override
  void paint(Canvas canvas, Size size) {
    final rect = Offset.zero & size;
    // Base: radial from slightly lighter navy (top-centre) to near-black.
    canvas.drawRect(
      rect,
      Paint()
        ..shader = RadialGradient(
          center: const Alignment(0, -0.55),
          radius: 1.25,
          colors: const [Color(0xFF0E1A30), PaiColors.bg1, PaiColors.bg0],
          stops: const [0, 0.5, 1],
        ).createShader(rect),
    );

    // Grid
    const step = 44.0;
    final grid = Paint()
      ..color = PaiColors.cyan.withValues(alpha: 0.035)
      ..strokeWidth = 1;
    final path = Path();
    for (double x = 0; x <= size.width; x += step) {
      path.moveTo(x, 0);
      path.lineTo(x, size.height);
    }
    for (double y = 0; y <= size.height; y += step) {
      path.moveTo(0, y);
      path.lineTo(size.width, y);
    }
    canvas.drawPath(path, grid);

    // Slow scanline
    final y = (t * (size.height + 240)) - 120;
    final band = Rect.fromLTWH(0, y - 60, size.width, 120);
    canvas.drawRect(
      band,
      Paint()
        ..shader = LinearGradient(
          begin: Alignment.topCenter,
          end: Alignment.bottomCenter,
          colors: [
            PaiColors.cyan.withValues(alpha: 0),
            PaiColors.cyan.withValues(alpha: 0.045),
            PaiColors.cyan.withValues(alpha: 0),
          ],
        ).createShader(band),
    );

    // Vignette
    canvas.drawRect(
      rect,
      Paint()
        ..shader = RadialGradient(
          radius: 0.95,
          colors: [Colors.transparent, Colors.black.withValues(alpha: 0.55)],
          stops: const [0.55, 1],
        ).createShader(rect),
    );
  }

  @override
  bool shouldRepaint(_BackgroundPainter old) => old.t != t;
}

/// Thin L-shaped corner marks, the signature "HUD frame" detail.
class HudCornerBrackets extends CustomPainter {
  const HudCornerBrackets({this.color = PaiColors.cyan, this.length = 10, this.inset = 0, this.stroke = 1.4});
  final Color color;
  final double length;
  final double inset;
  final double stroke;

  @override
  void paint(Canvas canvas, Size s) {
    final p = Paint()
      ..color = color
      ..style = PaintingStyle.stroke
      ..strokeWidth = stroke
      ..strokeCap = StrokeCap.square;
    final l = math.min(length, math.min(s.width, s.height) / 3);
    final r = Rect.fromLTWH(inset, inset, s.width - inset * 2, s.height - inset * 2);
    final path = Path()
      ..moveTo(r.left, r.top + l)..lineTo(r.left, r.top)..lineTo(r.left + l, r.top)
      ..moveTo(r.right - l, r.top)..lineTo(r.right, r.top)..lineTo(r.right, r.top + l)
      ..moveTo(r.right, r.bottom - l)..lineTo(r.right, r.bottom)..lineTo(r.right - l, r.bottom)
      ..moveTo(r.left + l, r.bottom)..lineTo(r.left, r.bottom)..lineTo(r.left, r.bottom - l);
    canvas.drawPath(path, p);
  }

  @override
  bool shouldRepaint(HudCornerBrackets old) =>
      old.color != color || old.length != length || old.inset != inset || old.stroke != stroke;
}
