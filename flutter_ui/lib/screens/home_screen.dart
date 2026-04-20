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
            const _StatusBar(),
            Padding(
              padding: const EdgeInsets.fromLTRB(12, 10, 12, 8),
              child: _ControlPanel(colors: colors),
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
              Expanded(
                child: Consumer<WebSocketService>(
                  builder: (context, ws, _) {
                    return Text(
                      ws.currentSong?.toString() ?? 'Floating Lyrics',
                      style: const TextStyle(
                        fontSize: 12,
                        fontWeight: FontWeight.w600,
                      ),
                      overflow: TextOverflow.ellipsis,
                    );
                  },
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

class _StatusBar extends StatelessWidget {
  const _StatusBar();

  @override
  Widget build(BuildContext context) {
    return Consumer<WebSocketService>(
      builder: (context, ws, _) {
        final connectedColor =
            ws.isConnected ? Colors.greenAccent : Colors.redAccent;
        return Container(
          width: double.infinity,
          margin: const EdgeInsets.fromLTRB(12, 10, 12, 0),
          padding: const EdgeInsets.symmetric(vertical: 10, horizontal: 12),
          decoration: BoxDecoration(
            color: Colors.white.withOpacity(0.04),
            borderRadius: BorderRadius.circular(12),
            border:
                Border.all(color: connectedColor.withOpacity(0.6), width: 1),
          ),
          child: Row(
            children: [
              Icon(Icons.circle, size: 9, color: connectedColor),
              const SizedBox(width: 8),
              Expanded(
                child: Text(
                  ws.status,
                  style: const TextStyle(
                      fontSize: 11, fontWeight: FontWeight.w500),
                  overflow: TextOverflow.ellipsis,
                ),
              ),
            ],
          ),
        );
      },
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
        final debugOnly = ws.isDebugOnly;

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
            const SizedBox(width: 8),
            Tooltip(
              message: debugOnly
                  ? 'Capturando áudio sem enviar para APIs'
                  : 'Capturar áudio sem gastar tokens',
              child: OutlinedButton.icon(
                onPressed: canControl ? ws.toggleDebugOnly : null,
                icon: Icon(
                  debugOnly ? Icons.bug_report : Icons.bug_report_outlined,
                  size: 18,
                ),
                label: Text(debugOnly ? 'Debug ON' : 'Debug'),
                style: OutlinedButton.styleFrom(
                  padding:
                      const EdgeInsets.symmetric(horizontal: 12, vertical: 12),
                  foregroundColor: debugOnly ? Colors.amber : Colors.white70,
                  side: BorderSide(
                    color: debugOnly
                        ? Colors.amber.withOpacity(0.6)
                        : Colors.white.withOpacity(0.2),
                  ),
                  shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(12),
                  ),
                ),
              ),
            ),
            const SizedBox(width: 8),
            OutlinedButton.icon(
              onPressed: ws.ping,
              icon: const Icon(Icons.network_ping_rounded),
              label: const Text('Ping'),
              style: OutlinedButton.styleFrom(
                padding:
                    const EdgeInsets.symmetric(horizontal: 12, vertical: 12),
                foregroundColor: Colors.white70,
                side: BorderSide(color: Colors.white.withOpacity(0.2)),
                shape: RoundedRectangleBorder(
                  borderRadius: BorderRadius.circular(12),
                ),
              ),
            ),
          ],
        );
      },
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
