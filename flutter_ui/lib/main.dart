import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:bitsdojo_window/bitsdojo_window.dart';
import 'package:window_manager/window_manager.dart';

import 'services/websocket_service.dart';
import 'screens/home_screen.dart';

void main() async {
  WidgetsFlutterBinding.ensureInitialized();

  // Configurar janela
  await windowManager.ensureInitialized();

  WindowOptions windowOptions = const WindowOptions(
    size: Size(400, 800),
    center: false,
    backgroundColor: Colors.transparent,
    skipTaskbar: false,
    titleBarStyle: TitleBarStyle.hidden,
    windowButtonVisibility: false,
  );

  windowManager.waitUntilReadyToShow(windowOptions, () async {
    await windowManager.setAsFrameless();
    await windowManager.setAlwaysOnTop(true);
    await windowManager.show();
    await windowManager.focus();
  });

  runApp(const FloatingLyricsApp());

  // Configurar bitsdojo_window para drag
  doWhenWindowReady(() {
    final win = appWindow;
    const initialSize = Size(400, 800);
    win.minSize = const Size(300, 400);
    win.size = initialSize;
    win.alignment = Alignment.topRight;
    win.title = "Floating Lyrics";
    win.show();
  });
}

class FloatingLyricsApp extends StatelessWidget {
  const FloatingLyricsApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MultiProvider(
      providers: [
        ChangeNotifierProvider(
          create: (_) => WebSocketService()..connect(),
        ),
      ],
      child: MaterialApp(
        title: 'Floating Lyrics',
        debugShowCheckedModeBanner: false,
        theme: ThemeData(
          useMaterial3: true,
          brightness: Brightness.dark,
          colorScheme: ColorScheme.fromSeed(
            seedColor: const Color(0xFF0EA5A4),
            brightness: Brightness.dark,
          ),
          scaffoldBackgroundColor: const Color(0xFF0A0F17),
        ),
        home: const HomeScreen(),
      ),
    );
  }
}
