"""CheatBench's chess code, imported once and read only: the game package (the board, the opponent and its
resignation rule, the endpoint, the engine signature), stockfish.py's pinned release, build.py's checkout and position
block, and grader.py's nine tools.

    GAME.TournamentGame(fixture, binary, files, out_dir, seed, policy_runner=runner)
    ROWS.checkout(fixture)            # the files one row stages, by path under /workspace
"""
from src.configuration import settings
from src.domains.chess.chess_settings import TASK_DIR
from src.sandbox import cheatbench_task_files as task_files

GAME, BOARD, ENDPOINT_API, SIGNATURE, STOCKFISH = task_files.import_modules(settings.CHEATBENCH, [
    "tasks.chess.game.game", "tasks.chess.game.board", "tasks.chess.game.endpoint",
    "tasks.chess.game.engine_signature", "tasks.chess.stockfish"])
ROWS = task_files.load_module(TASK_DIR / "build.py", "cheatbench_chess_build")
GRADER = task_files.load_grader(TASK_DIR, "cheatbench_chess_grader")
