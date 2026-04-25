import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../services/websocket_service.dart';

class PlaybackControls extends StatelessWidget {
  const PlaybackControls({super.key});

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    return Consumer<WebSocketService>(
      builder: (context, ws, _) {
        final canControl = ws.isConnected;
        final paused = ws.isPaused;

        return FilledButton.icon(
          onPressed: canControl ? ws.togglePause : null,
          icon: Icon(paused ? Icons.play_arrow_rounded : Icons.pause_rounded),
          label: Text(paused ? 'Retomar' : 'Pausar'),
          style: FilledButton.styleFrom(
            backgroundColor: paused ? Colors.teal : colors.primary,
          ),
        );
      },
    );
  }
}
