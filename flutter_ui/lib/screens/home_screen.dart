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
    return Scaffold(
      body: Column(
        children: [
          // Barra de título draggable
          WindowTitleBarBox(
            child: MoveWindow(
              child: Container(
                height: 40,
                decoration: BoxDecoration(
                  gradient: LinearGradient(
                    colors: [
                      Theme.of(context).colorScheme.primary,
                      Theme.of(context).colorScheme.secondary,
                    ],
                  ),
                ),
                child: Row(
                  children: [
                    const Padding(
                      padding: EdgeInsets.symmetric(horizontal: 12),
                      child: Icon(Icons.music_note, size: 20),
                    ),
                    Expanded(
                      child: Consumer<WebSocketService>(
                        builder: (context, ws, _) {
                          return Text(
                            ws.currentSong?.toString() ?? 'Floating Lyrics',
                            style: const TextStyle(
                              fontSize: 12,
                              fontWeight: FontWeight.w500,
                            ),
                            overflow: TextOverflow.ellipsis,
                          );
                        },
                      ),
                    ),
                    // Botões de controle
                    const WindowButtons(),
                  ],
                ),
              ),
            ),
          ),

          // Status bar
          Consumer<WebSocketService>(
            builder: (context, ws, _) {
              return Container(
                width: double.infinity,
                padding:
                    const EdgeInsets.symmetric(vertical: 8, horizontal: 12),
                decoration: BoxDecoration(
                  color: ws.isConnected
                      ? Colors.green.withOpacity(0.1)
                      : Colors.red.withOpacity(0.1),
                  border: Border(
                    bottom: BorderSide(
                      color: ws.isConnected ? Colors.green : Colors.red,
                      width: 1,
                    ),
                  ),
                ),
                child: Row(
                  children: [
                    Icon(
                      ws.isConnected ? Icons.circle : Icons.circle_outlined,
                      size: 8,
                      color: ws.isConnected ? Colors.green : Colors.red,
                    ),
                    const SizedBox(width: 8),
                    Expanded(
                      child: Text(
                        ws.status,
                        style: const TextStyle(fontSize: 11),
                        overflow: TextOverflow.ellipsis,
                      ),
                    ),
                  ],
                ),
              );
            },
          ),

          // Visualizador de áudio estilo retro
          Padding(
            padding: const EdgeInsets.all(12.0),
            child: Consumer<WebSocketService>(
              builder: (context, ws, _) {
                return AudioVisualizer(
                  isPlaying: ws.currentSong != null,
                  barColor: Theme.of(context).colorScheme.primary,
                );
              },
            ),
          ),

          // Conteúdo principal: Letras
          const Expanded(
            child: LyricsDisplay(),
          ),
        ],
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
