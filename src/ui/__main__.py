"""Serve the results browser in the foreground: python -m src.ui [--runs runs] [--port 8765]."""
from __future__ import annotations

import argparse
from pathlib import Path

from src.configuration import settings
from src.ui._server import serve


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--runs", type=Path, default=settings.RUNS)
    ap.add_argument("--port", type=int, default=settings.UI_PORT)
    args = ap.parse_args(argv)
    print(f"results browser: http://127.0.0.1:{args.port}/  (runs: {args.runs})", flush=True)
    serve(args.runs, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
