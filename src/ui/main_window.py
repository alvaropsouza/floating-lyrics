# LyricsWindow is now the primary window; keep MainWindow as an alias
# so main.py needs no import changes.
from src.ui.lyrics_window import LyricsWindow as MainWindow

__all__ = ["MainWindow"]

