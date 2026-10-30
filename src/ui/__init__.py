"""The results browser: a local single-page UI over runs/ and the eval logs the runs point at.

    start(8765)                  # in the background, as `python -m src ui` does
    stop(8765)
    serve(Path("runs"), port=8765)   # in the foreground, as `python -m src.ui` does
"""
from src.ui.launch import start, stop
from src.ui.server import serve

__all__ = ["serve", "start", "stop"]
