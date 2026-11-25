"""A run of the swarm as a 3D picture with a time slider, written as one HTML file and opened in the browser.

    python swarm_visualization/visualize_swarm.py ian_flipt_deepseek_n10_reg0_revealing_s1
    python swarm_visualization/visualize_swarm.py runs/regulator_scale_1/ian_flipt_deepseek_n10_reg0_revealing_s1
    python swarm_visualization/visualize_swarm.py <run> --out /some/where.html --no-open

Each node is an agent. Edges grow as agents talk to each other on the board, thicker the more they go back and
forth. Coloured orbs are the exploit's pieces: they circle their holders and fly to an agent that gets one it
lacked. Nodes turn red as their agents cheat. Needs only Python's standard library and a browser with WebGL.
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
from swarm_visualization.src.run_locator import find_run  # noqa: E402
from swarm_visualization.src.swarm_story import build_story  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run", help="a run folder, a run name (searched in runs/ and OLD/runs/), or study/name")
    parser.add_argument("--out", type=Path, help="the HTML file to write (default swarm_visualization/out/<run>.html)")
    parser.add_argument("--no-open", action="store_true", help="write the file without opening a browser")
    args = parser.parse_args(argv)

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
