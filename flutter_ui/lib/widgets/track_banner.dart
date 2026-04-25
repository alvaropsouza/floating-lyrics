import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../services/websocket_service.dart';
import '../theme/app_tokens.dart';

class TrackBanner extends StatelessWidget {
  const TrackBanner({super.key});

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
                AppColors.bannerOuterTop,
                AppColors.bannerOuterBottom,
              ],
            ),
            borderRadius: BorderRadius.circular(AppRadii.bannerOuter),
            border: Border.all(color: AppColors.bannerOuterBorder, width: 1),
            boxShadow: [
              BoxShadow(
                color: Colors.black.withValues(alpha: 0.35),
                blurRadius: 8,
                offset: const Offset(0, 2),
              ),
            ],
          ),
          child: Container(
            decoration: BoxDecoration(
              borderRadius: BorderRadius.circular(AppRadii.bannerInner),
              border: Border.all(color: AppColors.bannerInnerBorder, width: 1),
              gradient: const LinearGradient(
                begin: Alignment.topCenter,
                end: Alignment.bottomCenter,
                colors: [
                  AppColors.bannerInnerTop,
                  AppColors.bannerInnerBottom,
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
                            Colors.white.withValues(alpha: 0.14),
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
                          borderRadius: BorderRadius.circular(AppRadii.chip),
                          border: Border.all(
                            color: AppColors.bannerChipBorder,
                            width: 1,
                          ),
                          color: AppColors.bannerChipFill,
                        ),
                        child: const Text(
                          'SRC',
                          style: TextStyle(
                            color: AppColors.bannerChipText,
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
                                ? AppColors.bannerTrackText
                                : AppColors.bannerTrackIdleText,
                            fontWeight: FontWeight.w500,
                            shadows: const [
                              Shadow(
                                color: AppColors.bannerGlow,
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

  const _MarqueeText({required this.text, required this.style});

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
