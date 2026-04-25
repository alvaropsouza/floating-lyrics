import 'package:flutter/material.dart';

import '../theme/app_tokens.dart';

class TranslationToggle extends StatelessWidget {
  final bool value;
  final bool isLoading;
  final ValueChanged<bool> onChanged;

  const TranslationToggle({
    super.key,
    required this.value,
    required this.isLoading,
    required this.onChanged,
  });

  @override
  Widget build(BuildContext context) {
    final activeColor = value ? AppColors.translationActive : Colors.white38;

    return Row(
      mainAxisAlignment: MainAxisAlignment.end,
      crossAxisAlignment: CrossAxisAlignment.center,
      mainAxisSize: MainAxisSize.min,
      children: [
        Icon(Icons.translate_rounded, size: 12, color: activeColor),
        const SizedBox(width: 6),
        Text(
          'Traduzir automaticamente',
          style: TextStyle(
            fontSize: 10,
            color: activeColor,
            letterSpacing: 0.2,
          ),
        ),
        const SizedBox(width: 8),
        SizedBox(
          width: 40,
          height: 20,
          child: isLoading
              ? const Center(
                  child: SizedBox(
                    width: 12,
                    height: 12,
                    child: CircularProgressIndicator(
                      strokeWidth: 1.5,
                      color: Colors.white38,
                    ),
                  ),
                )
              : FittedBox(
                  fit: BoxFit.contain,
                  child: Switch(
                    value: value,
                    onChanged: onChanged,
                  ),
                ),
        ),
      ],
    );
  }
}
