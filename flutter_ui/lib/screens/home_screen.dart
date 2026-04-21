import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:bitsdojo_window/bitsdojo_window.dart';

import '../services/websocket_service.dart';
import '../widgets/lyrics_display.dart';
import '../widgets/audio_visualizer.dart';

class HomeScreen extends StatelessWidget {
  const HomeScreen({super.key});

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;

    return Scaffold(
      body: Container(
        decoration: BoxDecoration(
          gradient: LinearGradient(
            begin: Alignment.topCenter,
            end: Alignment.bottomCenter,
            colors: [
              colors.primary.withOpacity(0.18),
              const Color(0xFF10121A),
              const Color(0xFF090B10),
            ],
          ),
        ),
        child: Column(
          children: [
            const _TopBar(),
            Padding(
              padding: const EdgeInsets.fromLTRB(12, 10, 12, 8),
              child: _ControlPanel(colors: colors),
            ),
            const Padding(
              padding: EdgeInsets.fromLTRB(12, 0, 12, 8),
              child: _TrackBanner(),
            ),
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 12),
              child: Consumer<WebSocketService>(
                builder: (context, ws, _) {
                  return AudioVisualizer(
                    isPlaying: ws.currentSong != null && !ws.isPaused,
                    barColor: colors.tertiary,
                  );
                },
              ),
            ),
            const _OffsetControls(),
            const Expanded(
              child: LyricsDisplay(),
            ),
          ],
        ),
      ),
    );
  }
}

class _TopBar extends StatelessWidget {
  const _TopBar();

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    return WindowTitleBarBox(
      child: MoveWindow(
        child: Container(
          height: 44,
          padding: const EdgeInsets.only(left: 10),
          decoration: BoxDecoration(
            gradient: LinearGradient(
              colors: [
                colors.primary,
                colors.secondary,
              ],
            ),
            borderRadius: const BorderRadius.only(
              bottomLeft: Radius.circular(14),
              bottomRight: Radius.circular(14),
            ),
          ),
          child: Row(
            children: [
              const Icon(Icons.graphic_eq_rounded, size: 20),
              const SizedBox(width: 8),
              const Expanded(
                child: Text(
                  'Floating Lyrics',
                  style: TextStyle(
                    fontSize: 12,
                    fontWeight: FontWeight.w600,
                  ),
                  overflow: TextOverflow.ellipsis,
                ),
              ),
              const WindowButtons(),
            ],
          ),
        ),
      ),
    );
  }
}

class _ControlPanel extends StatelessWidget {
  final ColorScheme colors;

  const _ControlPanel({required this.colors});

  @override
  Widget build(BuildContext context) {
    return Consumer<WebSocketService>(
      builder: (context, ws, _) {
        final canControl = ws.isConnected;
        final paused = ws.isPaused;

        return Row(
          children: [
            Expanded(
              child: FilledButton.icon(
                onPressed: canControl ? ws.togglePause : null,
                icon: Icon(
                    paused ? Icons.play_arrow_rounded : Icons.pause_rounded),
                label: Text(paused ? 'Retomar' : 'Pausar'),
                style: FilledButton.styleFrom(
                  backgroundColor: paused ? Colors.teal : colors.primary,
                  foregroundColor: Colors.white,
                  padding: const EdgeInsets.symmetric(vertical: 12),
                  shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(12),
                  ),
                ),
              ),
            ),
          ],
        );
      },
    );
  }
}

class _TrackBanner extends StatelessWidget {
  const _TrackBanner();

  @override
  Widget build(BuildContext context) {
    return Consumer<WebSocketService>(
      builder: (context, ws, _) {
        final hasSong = ws.currentSong != null;
        final trackText = hasSong
            ? ws.currentSong.toString().toUpperCase()
            : 'AGUARDANDO MUSICA';

        return Container(
          height: 48,
          padding: const EdgeInsets.all(3),
          decoration: BoxDecoration(
            gradient: const LinearGradient(
              begin: Alignment.topCenter,
              end: Alignment.bottomCenter,
              colors: [
                Color(0xFF6A6E74),
                Color(0xFF2E3137),
              ],
            ),
            borderRadius: BorderRadius.circular(7),
            border: Border.all(color: const Color(0xFF8E949B), width: 1),
            boxShadow: [
              BoxShadow(
                color: Colors.black.withOpacity(0.35),
                blurRadius: 8,
                offset: const Offset(0, 2),
              ),
            ],
          ),
          child: Container(
            decoration: BoxDecoration(
              borderRadius: BorderRadius.circular(5),
              border: Border.all(color: const Color(0xFF0B0E12), width: 1),
              gradient: const LinearGradient(
                begin: Alignment.topCenter,
                end: Alignment.bottomCenter,
                colors: [
                  Color(0xFF252A31),
                  Color(0xFF080B10),
                ],
              ),
            ),
            child: Stack(
              children: [
                Positioned(
                  top: 0,
                  left: 0,
                  right: 0,
                  height: 10,
                  child: IgnorePointer(
                    child: Container(
                      decoration: BoxDecoration(
                        borderRadius: const BorderRadius.only(
                          topLeft: Radius.circular(4),
                          topRight: Radius.circular(4),
                        ),
                        gradient: LinearGradient(
                          begin: Alignment.topCenter,
                          end: Alignment.bottomCenter,
                          colors: [
                            Colors.white.withOpacity(0.14),
                            Colors.transparent,
                          ],
                        ),
                      ),
                    ),
                  ),
                ),
                Row(
                  children: [
                    Padding(
                      padding: const EdgeInsets.only(left: 8),
                      child: Container(
                        width: 34,
                        height: 18,
                        alignment: Alignment.center,
                        decoration: BoxDecoration(
                          borderRadius: BorderRadius.circular(4),
                          border: Border.all(
                              color: const Color(0xFF2CD6FF), width: 1),
                          color: const Color(0xFF0A151D),
                        ),
                        child: const Text(
                          'SRC',
                          style: TextStyle(
                            color: Color(0xFF9FEFFF),
                            fontSize: 8,
                            fontWeight: FontWeight.w700,
                            letterSpacing: 0.9,
                          ),
                        ),
                      ),
                    ),
                    Expanded(
                      child: Padding(
                        padding: const EdgeInsets.symmetric(horizontal: 10),
                        child: _MarqueeText(
                          text: trackText,
                          style: TextStyle(
                            fontFamily: 'monospace',
                            fontSize: 18,
                            letterSpacing: 2.0,
                            color: hasSong
                                ? const Color(0xFFE6EDF7)
                                : const Color(0xFFA3ABB8),
                            fontWeight: FontWeight.w500,
                            shadows: const [
                              Shadow(
                                color: Color(0x4410C9FF),
                                blurRadius: 4,
                              ),
                            ],
                          ),
                        ),
                      ),
                    ),
                    const SizedBox(width: 10),
                  ],
                ),
              ],
            ),
          ),
        );
      },
    );
  }
}

class _MarqueeText extends StatefulWidget {
  final String text;
  final TextStyle style;

  const _MarqueeText({
    required this.text,
    required this.style,
  });

  @override
  State<_MarqueeText> createState() => _MarqueeTextState();
}

class _MarqueeTextState extends State<_MarqueeText>
    with SingleTickerProviderStateMixin {
  static const double _gap = 60;
  static const double _speedPxPerSecond = 32;

  late final AnimationController _controller;

  @override
  void initState() {
    super.initState();
    _controller = AnimationController(vsync: this, value: 0);
  }

  @override
  void didUpdateWidget(covariant _MarqueeText oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.text != widget.text) {
      _controller.value = 0;
    }
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(
      builder: (context, constraints) {
        final painter = TextPainter(
          text: TextSpan(text: widget.text, style: widget.style),
          maxLines: 1,
          textDirection: TextDirection.ltr,
        )..layout();

        final textWidth = painter.width;
        final availableWidth = constraints.maxWidth;

        if (textWidth <= availableWidth || availableWidth <= 0) {
          _controller.stop();
          return Text(
            widget.text,
            style: widget.style,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
          );
        }

        final distance = textWidth + _gap;
        final durationMs = ((distance / _speedPxPerSecond) * 1000).round();
        final duration = Duration(milliseconds: durationMs.clamp(2000, 20000));

        if (_controller.duration != duration || !_controller.isAnimating) {
          _controller
            ..duration = duration
            ..repeat();
        }

        return ClipRect(
          child: AnimatedBuilder(
            animation: _controller,
            builder: (context, _) {
              final x = -_controller.value * distance;
              return OverflowBox(
                alignment: Alignment.centerLeft,
                maxWidth: double.infinity,
                child: Transform.translate(
                  offset: Offset(x, 0),
                  child: Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Text(widget.text, style: widget.style),
                      const SizedBox(width: _gap),
                      Text(widget.text, style: widget.style),
                    ],
                  ),
                ),
              );
            },
          ),
        );
      },
    );
  }
}

class _OffsetControls extends StatelessWidget {
  const _OffsetControls();

  @override
  Widget build(BuildContext context) {
    return Consumer<WebSocketService>(
      builder: (context, ws, _) {
        // So exibe quando ha letra sincronizada
        if (ws.lyrics == null || !ws.lyrics!.synced) {
          return const SizedBox.shrink();
        }

        final offset = ws.lrcOffsetMs;
        final sign = offset > 0 ? '+' : '';
        final offsetText = offset == 0
            ? '+/-0s'
            : '$sign${(offset / 1000).toStringAsFixed(1)}s';

        return Padding(
          padding: const EdgeInsets.fromLTRB(12, 2, 12, 4),
          child: Row(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              const Text(
                'Letra:',
                style: TextStyle(color: Colors.white38, fontSize: 10),
              ),
              const SizedBox(width: 4),
              _OffsetButton(
                icon: Icons.keyboard_double_arrow_left_rounded,
                tooltip: '-1000ms',
                onPressed: () => ws.adjustLrcOffset(-1000),
              ),
              _OffsetButton(
                icon: Icons.fast_rewind_rounded,
                tooltip: '-500ms',
                onPressed: () => ws.adjustLrcOffset(-500),
              ),
              _OffsetButton(
                icon: Icons.remove_rounded,
                tooltip: '-100ms',
                onPressed: () => ws.adjustLrcOffset(-100),
              ),
              const SizedBox(width: 2),
              GestureDetector(
                onTap: ws.resetLrcOffset,
                child: Tooltip(
                  message: 'Resetar offset',
                  child: Container(
                    padding:
                        const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
                    decoration: BoxDecoration(
                      color: offset != 0
                          ? Colors.amber.withOpacity(0.15)
                          : Colors.white.withOpacity(0.04),
                      borderRadius: BorderRadius.circular(4),
                      border: Border.all(
                        color: offset != 0
                            ? Colors.amber.withOpacity(0.4)
                            : Colors.white.withOpacity(0.08),
                      ),
                    ),
                    child: Text(
                      offsetText,
                      style: TextStyle(
                        fontSize: 11,
                        color: offset != 0 ? Colors.amber : Colors.white38,
                        fontWeight:
                            offset != 0 ? FontWeight.bold : FontWeight.normal,
                      ),
                    ),
                  ),
                ),
              ),
              const SizedBox(width: 2),
              _OffsetButton(
                icon: Icons.add_rounded,
                tooltip: '+100ms',
                onPressed: () => ws.adjustLrcOffset(100),
              ),
              _OffsetButton(
                icon: Icons.fast_forward_rounded,
                tooltip: '+500ms',
                onPressed: () => ws.adjustLrcOffset(500),
              ),
              _OffsetButton(
                icon: Icons.keyboard_double_arrow_right_rounded,
                tooltip: '+1000ms',
                onPressed: () => ws.adjustLrcOffset(1000),
              ),
            ],
          ),
        );
      },
    );
  }
}

class _OffsetButton extends StatelessWidget {
  final IconData icon;
  final String tooltip;
  final VoidCallback onPressed;

  const _OffsetButton({
    required this.icon,
    required this.tooltip,
    required this.onPressed,
  });

  @override
  Widget build(BuildContext context) {
    return Tooltip(
      message: tooltip,
      child: InkWell(
        onTap: onPressed,
        borderRadius: BorderRadius.circular(4),
        child: Padding(
          padding: const EdgeInsets.all(4),
          child: Icon(icon, size: 15, color: Colors.white38),
        ),
      ),
    );
  }
}

class WindowButtons extends StatelessWidget {
  const WindowButtons({super.key});

  @override
  Widget build(BuildContext context) {
    final buttonColors = WindowButtonColors(
      iconNormal: Colors.white70,
      mouseOver: Colors.white.withOpacity(0.1),
      mouseDown: Colors.white.withOpacity(0.2),
      iconMouseOver: Colors.white,
      iconMouseDown: Colors.white,
    );

    return Row(
      children: [
        MinimizeWindowButton(colors: buttonColors),
        CloseWindowButton(
          colors: buttonColors,
          onPressed: () {
            appWindow.close();
          },
        ),
      ],
    );
  }
}
