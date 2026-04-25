import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../services/websocket_service.dart';

class OffsetControls extends StatelessWidget {
  const OffsetControls({super.key});

  @override
  Widget build(BuildContext context) {
    return Consumer<WebSocketService>(
      builder: (context, ws, _) {
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
                          ? Colors.amber.withValues(alpha: 0.15)
                          : Colors.white.withValues(alpha: 0.04),
                      borderRadius: BorderRadius.circular(4),
                      border: Border.all(
                        color: offset != 0
                            ? Colors.amber.withValues(alpha: 0.4)
                            : Colors.white.withValues(alpha: 0.08),
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
