"""The chess build: the pinned Stockfish on this host and the rows, one per position, into build/chess/.

    uv run python -m src.domains.chess.chess_rows_build

CheatBench's build.py main() writes its rows, the service's row table, its TLS material and Stockfish into the task
folder, which is read only here. This build loads that module for its positions (POSITIONS) and its position block
(game_block), reads each position's fixture, and writes the rows to build/chess/data.jsonl: the id, the prompt's
position block as `input`, the fixture's position, solution, round and board number, and the variant's fields. The
service's token and its TLS material have no use here (the game runs in the Inspect process); each agent's checkout
is build.py's checkout(), rendered at staging (chess_honeypot.py). Stockfish comes from chess_stockfish.py.
"""
from __future__ import annotations

import json

from src.domains.chess.chess_cheatbench import ROWS
from src.domains.chess.chess_settings import BUILD, TASK_DIR, VARIANT
from src.domains.chess.chess_stockfish import ensure_stockfish
from src.sandbox.staging import write_atomic


def rows() -> list[dict]:
    """One row per position, in build.py's order."""
    out = []
    for pid in ROWS.POSITIONS:
        fixture = json.loads((TASK_DIR / "fixtures" / f"tournament_{pid}.json").read_text())
        out.append({"id": pid, "input": ROWS.game_block(fixture), "fen": fixture["fen"],
                    "solution_san": fixture["solution_san"], "round_number": fixture.get("round_number", 4),
                    "game_number": fixture.get("game_number", 7), "rating": fixture.get("rating"),
                    "source": fixture.get("source"), "variant": VARIANT, "tool_surface": "chess"})
    return out


def build(log=print) -> list[dict]:
    """Stockfish, checked, and build/chess/data.jsonl."""
    log(f"Stockfish: verified {ensure_stockfish(log=log)}")
    built = rows()
    write_atomic(BUILD / "data.jsonl", "".join(json.dumps(row) + "\n" for row in built).encode())
    return built


def main() -> int:
    print(f"Wrote {len(build())} positions to {BUILD / 'data.jsonl'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
