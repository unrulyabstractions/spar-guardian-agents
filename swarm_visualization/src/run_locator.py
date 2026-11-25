"""Find a run's folder from a path or a run name.

    find_run("runs/regulator_scale_1/ian_flipt_deepseek_n10_reg0_revealing_s1")   # a path
    find_run("ian_flipt_deepseek_n10_reg0_revealing_s1")                          # a name: runs/, OLD/runs/
    find_run("regulator_scale_1/ian_flipt_deepseek_n10_reg0_revealing_s1")        # study/name
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SEARCH_ROOTS = (REPO_ROOT / "runs", REPO_ROOT / "OLD" / "runs")


def is_run(folder: Path) -> bool:
    return (folder / "processed" / "run.json").is_file()


def find_run(target: str) -> Path:
    """The run folder `target` names. Raises SystemExit with the candidates when the name is missing or ambiguous."""
    direct = Path(target).expanduser()
    for candidate in (direct, REPO_ROOT / direct):
        if candidate.is_dir() and is_run(candidate):
            return candidate.resolve()
    name = target.strip("/")
    matches: list[Path] = []
    for root in SEARCH_ROOTS:
        if not root.is_dir():
            continue
        if "/" in name:
            candidate = root / name
            if is_run(candidate):
                matches.append(candidate)
            continue
        matches += [p for p in root.glob(f"*/{name}") if is_run(p)]
        matches += [p for p in root.glob(f"*/*/{name}") if is_run(p)]
    matches = sorted(set(m.resolve() for m in matches))
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise SystemExit(f"no run named {target!r} under {', '.join(str(r) for r in SEARCH_ROOTS)}")
    listing = "\n  ".join(str(m) for m in matches)
    raise SystemExit(f"{target!r} names {len(matches)} runs; give the path:\n  {listing}")
