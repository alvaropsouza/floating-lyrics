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

class _AudioVisualizerState extends State<AudioVisualizer> {
  late List<double> _barHeights;

  @override
  void initState() {
    super.initState();
    _barHeights = List.generate(widget.barCount, (_) => 0.1);
  }

  void _updateSpectrum(List<double> spectrum) {
    // Verificar se há dados de espectro válidos (não todos zeros ou ruído)
    final hasValidSpectrum = spectrum.isNotEmpty && 
                             spectrum.any((v) => v > 0.1); // Threshold maior para ignorar ruído
    
    if (hasValidSpectrum) {
      // Usar dados reais do backend
      _barHeights = List.from(spectrum);
    } else {
      // Decay suave quando não há áudio - barras vão para quase zero
      for (int i = 0; i < _barHeights.length; i++) {
        _barHeights[i] *= 0.75; // Decay mais rápido
        if (_barHeights[i] < 0.02) _barHeights[i] = 0.01; // Mínimo bem baixo
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return Consumer<WebSocketService>(
      builder: (context, wsService, child) {
        // Atualizar espectro a cada rebuild (quando service notifica)
        _updateSpectrum(wsService.audioSpectrum);
        
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
            border: Border.all(
              color: widget.barColor.withOpacity(0.3),
              width: 1,
            ),
            borderRadius: BorderRadius.circular(8),
          ),
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 12),
            child: Row(
              mainAxisAlignment: MainAxisAlignment.spaceEvenly,
              crossAxisAlignment: CrossAxisAlignment.end,
              children: List.generate(
                widget.barCount,
                (index) => _buildBar(index),
              ),
            ),
          ),
        );
      },
    );
  }

  Widget _buildBar(int index) {
    final height = _barHeights[index];
    final maxHeight = 100.0;
    
    return AnimatedContainer(
      duration: const Duration(milliseconds: 100),
      curve: Curves.easeOut,
      width: 4,
      height: height * maxHeight,
      decoration: BoxDecoration(
        gradient: LinearGradient(
          begin: Alignment.bottomCenter,
          end: Alignment.topCenter,
          colors: [
            widget.barColor,
            widget.barColor.withOpacity(0.5),
          ],
        ),
        borderRadius: BorderRadius.circular(2),
        boxShadow: [
          BoxShadow(
            color: widget.barColor.withOpacity(0.5),
            blurRadius: 4,
            spreadRadius: 0,
          ),
        ],
      ),
    );
  }
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
  bool shouldRepaint(_WavePainter oldDelegate) => true;
}
