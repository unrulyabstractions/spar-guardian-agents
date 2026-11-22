"""Serve the results browser in the foreground: python -m src.ui [--runs runs] [--port 8765]."""
from __future__ import annotations

import argparse
from pathlib import Path

from src.configuration import settings
from src.ui._server import serve
from src.utils import progress


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--runs", type=Path, default=settings.RUNS)
    ap.add_argument("--port", type=int, default=settings.UI_PORT)
    args = ap.parse_args(argv)
    progress.step("Results browser")
    progress.detail("runs", args.runs)
    progress.done(f"the results browser serves http://127.0.0.1:{args.port}/ (Ctrl-C stops it)", word="Ready")
    serve(args.runs, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
