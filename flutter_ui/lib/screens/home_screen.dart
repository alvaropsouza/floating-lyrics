import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../services/websocket_service.dart';
import '../theme/app_tokens.dart';
import '../widgets/offset_controls.dart';
import '../widgets/playback_controls.dart';
import '../widgets/track_banner.dart';
import '../widgets/window_top_bar.dart';
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
              colors.primary.withValues(alpha: 0.18),
              AppColors.shellBackgroundTop,
              AppColors.shellBackgroundBottom,
            ],
          ),
        ),
        child: Column(
          children: [
            const WindowTopBar(),
            Padding(
              padding: const EdgeInsets.fromLTRB(
                AppSpacing.pageHorizontal,
                10,
                AppSpacing.pageHorizontal,
                AppSpacing.sectionGap,
              ),
              child: const PlaybackControls(),
            ),
            const Padding(
              padding: EdgeInsets.fromLTRB(
                AppSpacing.pageHorizontal,
                0,
                AppSpacing.pageHorizontal,
                AppSpacing.sectionGap,
              ),
              child: TrackBanner(),
            ),
            Padding(
              padding: const EdgeInsets.symmetric(
                horizontal: AppSpacing.pageHorizontal,
              ),
              child: Consumer<WebSocketService>(
                builder: (context, ws, _) {
                  return AudioVisualizer(
                    isPlaying: ws.currentSong != null && !ws.isPaused,
                    barColor: colors.tertiary,
                  );
                },
              ),
            ),
            const OffsetControls(),
            const Expanded(
              child: LyricsDisplay(),
            ),
          ],
        ),
      ),
    );
  }
}
