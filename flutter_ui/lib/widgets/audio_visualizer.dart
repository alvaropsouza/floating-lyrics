import 'dart:math';
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../services/websocket_service.dart';

class AudioVisualizer extends StatefulWidget {
  final bool isPlaying;
  final Color barColor;
  final int barCount;

  const AudioVisualizer({
    super.key,
    this.isPlaying = false,
    this.barColor = const Color(0xFF00FF00), // Verde estilo retro
    this.barCount = 32,
  });

  @override
  State<AudioVisualizer> createState() => _AudioVisualizerState();
}

class _AudioVisualizerState extends State<AudioVisualizer>
    with SingleTickerProviderStateMixin {
  late List<double> _barHeights;
  late AnimationController _ticker;

  @override
  void initState() {
    super.initState();
    _barHeights = List.filled(widget.barCount, 0.0);

    // Ticker de 60 FPS: interpola as barras em direção ao espectro mais recente
    // sem depender do ritmo de notificações WebSocket.
    _ticker = AnimationController(
      vsync: this,
      duration: const Duration(seconds: 1),
    )
      ..addListener(_onTick)
      ..repeat();
  }

  void _onTick() {
    // Lê o último espectro sem subscrever (sem rebuild cascata)
    final spectrum = context.read<WebSocketService>().audioSpectrum;

    bool changed = false;
    for (int i = 0; i < _barHeights.length; i++) {
      final target = i < spectrum.length ? spectrum[i].clamp(0.0, 1.0) : 0.0;
      final prev = _barHeights[i];
      // Subida rápida (0.7), descida mais suave (0.2)
      final alpha = target > prev ? 0.85 : 0.45;
      final next = prev + (target - prev) * alpha;
      _barHeights[i] = next < 0.005 ? 0.0 : next;
      if ((_barHeights[i] - prev).abs() > 0.001) changed = true;
    }

    if (changed) setState(() {});
  }

  @override
  void dispose() {
    _ticker.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Container(
      height: 120,
      decoration: BoxDecoration(
        gradient: LinearGradient(
          begin: Alignment.topCenter,
          end: Alignment.bottomCenter,
          colors: [
            Colors.black.withOpacity(0.8),
            Colors.black.withOpacity(0.4),
          ],
        ),
        border: Border.all(color: widget.barColor.withOpacity(0.3)),
        borderRadius: BorderRadius.circular(8),
      ),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 12),
        child: CustomPaint(
          size: Size.infinite,
          painter: _BarsPainter(
            heights: List.unmodifiable(_barHeights),
            barCount: widget.barCount,
            color: widget.barColor,
          ),
        ),
      ),
    );
  }
}

class _BarsPainter extends CustomPainter {
  final List<double> heights;
  final int barCount;
  final Color color;

  _BarsPainter({
    required this.heights,
    required this.barCount,
    required this.color,
  });

  @override
  void paint(Canvas canvas, Size size) {
    if (heights.isEmpty) return;

    final barWidth = 4.0;
    final spacing = (size.width - barWidth * barCount) / (barCount + 1);
    final maxHeight = size.height;

    final paintBar = Paint()..style = PaintingStyle.fill;
    final paintGlow = Paint()
      ..color = color.withOpacity(0.35)
      ..style = PaintingStyle.fill
      ..maskFilter = const MaskFilter.blur(BlurStyle.normal, 3);

    for (int i = 0; i < barCount; i++) {
      final h = (heights[i].clamp(0.0, 1.0)) * maxHeight;
      final x = spacing + i * (barWidth + spacing);
      final rect = RRect.fromRectAndRadius(
        Rect.fromLTWH(x, maxHeight - h, barWidth, h),
        const Radius.circular(2),
      );

      // Gradiente por barra (do bottom-color ao top-color)
      paintBar.shader = LinearGradient(
        begin: Alignment.bottomCenter,
        end: Alignment.topCenter,
        colors: [color, color.withOpacity(0.5)],
      ).createShader(Rect.fromLTWH(x, maxHeight - h, barWidth, h));

      canvas.drawRRect(rect, paintGlow);
      canvas.drawRRect(rect, paintBar);
    }
  }

  @override
  bool shouldRepaint(_BarsPainter old) => true;
}

/// Visualizer estilo onda/waveform (alternativo)
class WaveVisualizer extends StatefulWidget {
  final bool isPlaying;
  final Color waveColor;

  const WaveVisualizer({
    super.key,
    this.isPlaying = false,
    this.waveColor = const Color(0xFF00FFFF), // Ciano retro
  });

  @override
  State<WaveVisualizer> createState() => _WaveVisualizerState();
}

class _WaveVisualizerState extends State<WaveVisualizer>
    with SingleTickerProviderStateMixin {
  late AnimationController _controller;
  final List<double> _wavePoints = [];
  final Random _random = Random();

  @override
  void initState() {
    super.initState();
    _initWavePoints();

    _controller = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 50),
    )..addListener(() {
        if (widget.isPlaying) {
          _updateWave();
        }
      });

    _controller.repeat();
  }

  void _initWavePoints() {
    _wavePoints.clear();
    for (int i = 0; i < 100; i++) {
      _wavePoints.add(0.5);
    }
  }

  void _updateWave() {
    setState(() {
      // Shift wave left
      _wavePoints.removeAt(0);

      if (widget.isPlaying) {
        // Add new random point
        _wavePoints.add(0.3 + _random.nextDouble() * 0.4);
      } else {
        _wavePoints.add(0.5);
      }
    });
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Container(
      height: 80,
      decoration: BoxDecoration(
        color: Colors.black.withOpacity(0.8),
        border: Border.all(
          color: widget.waveColor.withOpacity(0.3),
          width: 1,
        ),
        borderRadius: BorderRadius.circular(8),
      ),
      child: CustomPaint(
        painter: _WavePainter(
          points: _wavePoints,
          color: widget.waveColor,
        ),
        size: Size.infinite,
      ),
    );
  }
}

class _WavePainter extends CustomPainter {
  final List<double> points;
  final Color color;

  _WavePainter({required this.points, required this.color});

  @override
  void paint(Canvas canvas, Size size) {
    final paint = Paint()
      ..color = color
      ..strokeWidth = 2
      ..style = PaintingStyle.stroke
      ..strokeCap = StrokeCap.round;

    final path = Path();
    final step = size.width / (points.length - 1);

    for (int i = 0; i < points.length; i++) {
      final x = i * step;
      final y = size.height * points[i];

      if (i == 0) {
        path.moveTo(x, y);
      } else {
        path.lineTo(x, y);
      }
    }

    canvas.drawPath(path, paint);

    // Glow effect
    final glowPaint = Paint()
      ..color = color.withOpacity(0.3)
      ..strokeWidth = 6
      ..style = PaintingStyle.stroke
      ..maskFilter = const MaskFilter.blur(BlurStyle.normal, 3);

    canvas.drawPath(path, glowPaint);
  }

  @override
  bool shouldRepaint(_WavePainter oldDelegate) =>
      oldDelegate.points != points || oldDelegate.color != color;
}
