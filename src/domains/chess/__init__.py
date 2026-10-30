"""CheatBench's chess task for one position, as a Benchmark.

API modules:
- chess_task.py: the Chess Benchmark.
- chess_rows_build.py: the pinned Stockfish and the rows, built into build/chess/.
"""
from src.domains.chess.chess_task import Chess

__all__ = ["Chess"]
