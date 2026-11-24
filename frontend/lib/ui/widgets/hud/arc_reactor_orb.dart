import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../../theme/theme.dart';

/// What the assistant is doing, as the orb shows it.
enum OrbState { idle, listening, thinking, speaking }

extension OrbStateX on OrbState {
  String get label => switch (this) {
        OrbState.idle => 'Standing by',
        OrbState.listening => 'Listening',
        OrbState.thinking => 'Processing',
        OrbState.speaking => 'Speaking',
      };

  Color get color => switch (this) {
        OrbState.idle => PaiColors.cyanDim,
        OrbState.listening => PaiColors.teal,
        OrbState.thinking => PaiColors.azure,
        OrbState.speaking => PaiColors.cyan,
      };

  /// Same hues, darkened on light surfaces so the rings stay visible.
  Color colorOf(BuildContext context) {
    if (context.pai.dark) return color;
    return switch (this) {
      OrbState.idle => const Color(0xFF00727F),
      OrbState.listening => const Color(0xFF00796B),
      OrbState.thinking => const Color(0xFF1F5FD6),
      OrbState.speaking => const Color(0xFF00838F),
    };
  }
}

/// Jarvis-style "arc reactor": concentric rotating arcs, tick ring and a radial waveform
/// that follows [level] (0..1, e.g. microphone or playback loudness).
///
/// One [AnimationController] drives everything; the painter is isolated in a [RepaintBoundary];
/// with reduced-motion (or [animate] = false) it renders one still frame.
class ArcReactorOrb extends StatefulWidget {
  const ArcReactorOrb({
    super.key,
    required this.state,
    this.level = 0,
    this.size = 280,
    this.animate = true,
  });

  final OrbState state;
  final double level;
  final double size;
  final bool animate;

  @override
  State<ArcReactorOrb> createState() => _ArcReactorOrbState();
}

class _ArcReactorOrbState extends State<ArcReactorOrb> with SingleTickerProviderStateMixin {
  late final AnimationController _c =
      AnimationController(vsync: this, duration: const Duration(seconds: 60));
  double _smooth = 0;
  double _seconds = 0.8; // a pleasant still frame

  @override
  void initState() {
    super.initState();
    _c.addListener(_tick);
  }

  void _tick() {
    _seconds = _c.value * 60;
    _smooth += (widget.level.clamp(0.0, 1.0) - _smooth) * 0.22;
  }

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
  void didUpdateWidget(ArcReactorOrb old) {
    super.didUpdateWidget(old);
    didChangeDependencies();
    if (!_c.isAnimating) _smooth = widget.level.clamp(0.0, 1.0);
  }

  @override
  void dispose() {
    _c.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Semantics(
      label: 'Assistant: ${widget.state.label}',
      liveRegion: true,
      child: SizedBox.square(
        dimension: widget.size,
        child: RepaintBoundary(
          child: AnimatedBuilder(
            animation: _c,
            builder: (_, _) => CustomPaint(
              painter: _OrbPainter(widget.state, _smooth, _seconds, widget.state.colorOf(context)),
            ),
          ),
        ),
      ),
    );
  }
}

class _OrbPainter extends CustomPainter {
  _OrbPainter(this.state, this.level, this.t, this.color);
  final Color color;
  final OrbState state;
  final double level;
  final double t;

  double get _spin => switch (state) {
        OrbState.idle => 0.12,
        OrbState.listening => 0.28,
        OrbState.thinking => 1.5,
        OrbState.speaking => 0.45,
      };

  @override
  void paint(Canvas canvas, Size size) {
    final c = size.center(Offset.zero);
    final r = size.shortestSide / 2;
    final breathe = 0.5 + 0.5 * math.sin(t * (state == OrbState.thinking ? 4.2 : 1.6));
    final energy = switch (state) {
      OrbState.idle => 0.12 + 0.08 * breathe,
      OrbState.listening => 0.18 + 0.82 * level,
      OrbState.thinking => 0.45 + 0.25 * breathe,
      OrbState.speaking => 0.25 + 0.75 * level,
    };

    // 1. Halo
    canvas.drawCircle(
      c, r,
      Paint()
        ..shader = RadialGradient(colors: [
          color.withValues(alpha: 0.10 + 0.22 * energy),
          color.withValues(alpha: 0.0),
        ]).createShader(Rect.fromCircle(center: c, radius: r)),
    );

    // 2. Tick ring (outermost)
    final tick = Paint()..color = color.withValues(alpha: 0.55)..strokeWidth = 1.2;
    final tickRot = t * 0.06;
    for (var i = 0; i < 90; i++) {
      final a = tickRot + i * 2 * math.pi / 90;
      final long = i % 5 == 0;
      final r1 = r * 0.955;
      final r2 = r * (long ? 0.895 : 0.925);
      canvas.drawLine(c + Offset(math.cos(a), math.sin(a)) * r1, c + Offset(math.cos(a), math.sin(a)) * r2,
          tick..color = color.withValues(alpha: long ? 0.8 : 0.35));
    }

    // 3. Segmented arcs (rotating)
    final arc = Paint()
      ..style = PaintingStyle.stroke
      ..strokeCap = StrokeCap.round
      ..strokeWidth = r * 0.018;
    final r3 = r * 0.80;
    for (var i = 0; i < 3; i++) {
      arc.color = color.withValues(alpha: 0.85 - i * 0.05);
      canvas.drawArc(Rect.fromCircle(center: c, radius: r3), t * _spin + i * 2 * math.pi / 3,
          math.pi * (state == OrbState.thinking ? 0.42 : 0.5), false, arc);
    }

    // 4. Counter-rotating dashed ring
    final dash = Paint()
      ..style = PaintingStyle.stroke
      ..strokeWidth = r * 0.012
      ..color = color.withValues(alpha: 0.5);
    final r4 = r * 0.69;
    for (var i = 0; i < 28; i++) {
      canvas.drawArc(Rect.fromCircle(center: c, radius: r4), -t * _spin * 0.7 + i * 2 * math.pi / 28,
          math.pi / 28 * 0.9, false, dash);
    }

    // 5. Radial waveform
    const bars = 64;
    final wave = Paint()..strokeCap = StrokeCap.round..strokeWidth = r * 0.016;
    final r5 = r * 0.50;
    for (var i = 0; i < bars; i++) {
      final a = i * 2 * math.pi / bars - math.pi / 2;
      final noise = 0.5 + 0.5 * math.sin(i * 12.9898 + t * (state == OrbState.speaking ? 9 : 6) + math.sin(i * 0.7 + t * 2));
      double amp;
      switch (state) {
        case OrbState.thinking:
          final chase = (math.sin(a * 2 - t * 5) + 1) / 2;
          amp = 0.10 + 0.55 * chase * chase;
        case OrbState.idle:
          amp = 0.06 + 0.06 * breathe;
        case OrbState.listening:
        case OrbState.speaking:
          amp = 0.08 + level * (0.35 + 0.65 * noise);
      }
      final len = r * (0.02 + 0.17 * amp.clamp(0.0, 1.0));
      wave.color = color.withValues(alpha: 0.35 + 0.6 * amp.clamp(0.0, 1.0));
      canvas.drawLine(c + Offset(math.cos(a), math.sin(a)) * r5, c + Offset(math.cos(a), math.sin(a)) * (r5 + len), wave);
    }

    // 6. Inner ring + hexagon
    canvas.drawCircle(
        c, r * 0.455,
        Paint()
          ..style = PaintingStyle.stroke
          ..strokeWidth = 1.2
          ..color = color.withValues(alpha: 0.6));
    final hex = Path();
    for (var i = 0; i < 6; i++) {
      final a = -t * 0.2 + i * math.pi / 3;
      final p = c + Offset(math.cos(a), math.sin(a)) * r * 0.30;
      i == 0 ? hex.moveTo(p.dx, p.dy) : hex.lineTo(p.dx, p.dy);
    }
    hex.close();
    canvas.drawPath(hex, Paint()..style = PaintingStyle.stroke..strokeWidth = 1.4..color = color.withValues(alpha: 0.55));

    // 7. Core
    final coreR = r * (0.17 + 0.05 * energy + 0.012 * breathe);
    canvas.drawCircle(
      c, coreR * 2.2,
      Paint()
        ..shader = RadialGradient(colors: [color.withValues(alpha: 0.45 * (0.5 + energy)), color.withValues(alpha: 0)])
            .createShader(Rect.fromCircle(center: c, radius: coreR * 2.2)),
    );
    canvas.drawCircle(
      c, coreR,
      Paint()
        ..shader = RadialGradient(colors: [Colors.white, color, color.withValues(alpha: 0.55)], stops: const [0, 0.45, 1])
            .createShader(Rect.fromCircle(center: c, radius: coreR)),
    );
  }

  @override
  bool shouldRepaint(_OrbPainter old) => old.t != t || old.level != level || old.state != state || old.color != color;
}
