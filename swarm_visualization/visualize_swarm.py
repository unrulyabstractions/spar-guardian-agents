"""A run of the swarm as a 3D picture with a time slider, written as one HTML file and opened in the browser.

    python swarm_visualization/visualize_swarm.py ian_flipt_deepseek_n10_reg0_revealing_s1
    python swarm_visualization/visualize_swarm.py runs/regulator_scale_1/ian_flipt_deepseek_n10_reg0_revealing_s1
    python swarm_visualization/visualize_swarm.py <run> --out /some/where.html --no-open
    python swarm_visualization/visualize_swarm.py --study runs/regulator_scale_2

Each node is an agent. Edges grow as agents talk to each other on the board, thicker the more they go back and
forth. Coloured orbs are the exploit's pieces: they circle their holders and fly to an agent that gets one it
lacked. Nodes turn red as their agents cheat. Needs only Python's standard library and a browser with WebGL.

--study writes out/<study>/index.html, one grid of the study's runs, with every run's page beside it.
"""
from __future__ import annotations

import argparse
import sys
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from swarm_visualization.src.page_builder import write_page  # noqa: E402
from swarm_visualization.src.run_files import RunFiles  # noqa: E402
from swarm_visualization.src.run_locator import SEARCH_ROOTS, find_run  # noqa: E402
from swarm_visualization.src.study_page import write_study  # noqa: E402
from swarm_visualization.src.swarm_story import build_story  # noqa: E402


def find_study(target: str) -> Path:
    direct = Path(target).expanduser()
    for candidate in (direct, *(root / target for root in SEARCH_ROOTS)):
        if candidate.is_dir():
            return candidate.resolve()
    raise SystemExit(f"no study folder {target!r}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run", nargs="?",
                        help="a run folder, a run name (searched in runs/ and OLD/runs/), or study/name")
    parser.add_argument("--study", help="a study folder (or a study name in runs/ or OLD/runs/): the grid of its runs")
    parser.add_argument("--index-only", action="store_true", help="with --study, write only index.html")
    parser.add_argument("--out", type=Path, help="the HTML file to write (default swarm_visualization/out/<run>.html)")
    parser.add_argument("--no-open", action="store_true", help="write the file without opening a browser")
    args = parser.parse_args(argv)

    if args.study:
        study = find_study(args.study)
        out_dir = args.out or HERE / "out" / study.name
        index = write_study(study, out_dir, runs_pages=not args.index_only)
        print(f"{study}\n  wrote {index.resolve()}")
        if not args.no_open:
            webbrowser.open(index.resolve().as_uri())
        return 0
    if not args.run:
        parser.error("give a run, or --study")
    folder = find_run(args.run)
    story = build_story(RunFiles(folder))
    out = args.out or HERE / "out" / f"{folder.name}.html"
    write_page(story, out)
    cheated = sum(1 for a in story["agents"] if a["rungs"][-1][1] >= 3)
    print(f"{folder}\n  {len(story['agents'])} agents, {len(story['talk'])} exchanges, {len(story['posts'])} posts, "
          f"{len(story['flows'])} pieces passed, {cheated} agents cheated\n  wrote {out.resolve()}")
    if not args.no_open:
        webbrowser.open(out.resolve().as_uri())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
