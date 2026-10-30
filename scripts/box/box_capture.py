"""Prove that every file a box made has a local copy, before the box is destroyed.

    scripts/box/capture_check.sh <instance-id> [--pull]
    uv run python -m scripts.box.box_capture check <instance-id> [--pull]
    uv run python -m scripts.box.box_capture baseline <instance-id>

check sends box_sweep.py to the box over ssh (logs/boxes/<id>/ssh_config) and holds each line of its sweep against
this machine. A box file under the clone's runs/, logs/ or out/ must be at the same path in this repository, where
watch.sh brings it; any other box file must be at logs/boxes/<id>/files/<its absolute path>. A file is captured when
its size and sha256 match, a link when the local link has the same target. A file of the host image (the same path
and bytes as at first contact, in logs/boxes/<id>/baseline.jsonl) is counted and not required. The check fails on a
file missing or different, a path the sweep could not read, a process still producing, the clone at a commit
origin/main lacks, a submodule away from the commit the repository records, a sweep cut short, or a sweep that found
nothing. It deletes the old marker first and writes logs/boxes/<id>/capture.json only when it passes; fleet.py
destroy requires that marker, fresh.

--pull first fetches what is missing or different with rsync over the same ssh config: the outputs into this
repository with --update, as watch.sh does, so a newer local file is never overwritten and stays a difference; the
rest into logs/boxes/<id>/files/. It then sweeps the box again and holds the new sweep to the same rules.

baseline records the sweep of the box's home and /tmp before box_setup.sh changes anything. It runs once per box: a
later call keeps the first.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from scripts.box.fleet_layout import BOXES, REMOTE_REPO, ROOT

SWEEP = Path(__file__).with_name("box_sweep.py")
OUTPUT_DIRS = ("runs", "logs", "out")
HOME, TMP = "/root", "/tmp"
"""The box's home and /tmp, swept with the clone at REMOTE_REPO."""
SWEEP_TIMEOUT_S = 3600


def box_dir(instance_id: int) -> Path:
    return BOXES / str(instance_id)


def marker_path(instance_id: int) -> Path:
    return box_dir(instance_id) / "capture.json"


def read_marker(instance_id: int) -> dict | None:
    path = marker_path(instance_id)
    return json.loads(path.read_text()) if path.exists() else None


def ssh_config(instance_id: int) -> Path:
    config = box_dir(instance_id) / "ssh_config"
    if not config.exists():
        raise FileNotFoundError(f"{config} is missing: run `uv run python -m scripts.box.fleet status` first")
    if any(c.isspace() for c in str(config)):
        raise ValueError(f"{config}: rsync's -e cannot take a path with whitespace")
    return config


def output_rel(box_path: str) -> str | None:
    """The path under the clone of a box file in runs/, logs/ or out/; None for any other file."""
    rel = os.path.relpath(box_path, REMOTE_REPO)
    top, _, rest = rel.partition("/")
    return rel if top in OUTPUT_DIRS and rest else None


def local_path(box_path: str, folder: Path) -> Path:
    """Where this machine keeps a box file: the same path in this repository for an output, else
    <folder>/files/<its absolute path>."""
    rel = output_rel(box_path)
    return ROOT / rel if rel else folder / "files" / box_path.lstrip("/")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass
class Report:
    """A sweep held against this machine. swept = captured + image + lost + the excluded files."""

    swept: int = 0
    captured: int = 0
    image: int = 0
    missing: list[tuple[str, str]] = field(default_factory=list)
    """(box path, what it is) of each file with no local copy."""
    differ: list[tuple[str, str]] = field(default_factory=list)
    """(box path, how it differs) of each file whose local copy differs."""
    problems: list[str] = field(default_factory=list)
    excluded: dict[str, dict] = field(default_factory=dict)
    trees: list[dict] = field(default_factory=list)
    special: list[str] = field(default_factory=list)
    containers: int | None = None

    @property
    def lost(self) -> int:
        return len(self.missing) + len(self.differ)

    @property
    def ok(self) -> bool:
        return not self.lost and not self.problems and self.swept > 0

    def summary(self) -> str:
        excluded = sum(n["files"] for n in self.excluded.values())
        return (f"swept {self.swept} files: {self.captured} captured, {self.image} of the host image, {excluded} "
                f"excluded as reproducible, {len(self.missing)} missing, {len(self.differ)} different")


def compare(lines: list[dict], local_for: Callable[[str], Path], baseline: frozenset = frozenset(),
            on_origin: Callable[[str], bool] = lambda sha: False) -> Report:
    """Hold a sweep's lines against the local copies that local_for names."""
    report, done = Report(), False
    for line in lines:
        if "file" in line:
            report.swept += 1
            if (line["file"], line["size"], line["sha256"]) in baseline:
                report.image += 1
                continue
            local = local_for(line["file"])
            if local.is_symlink() or not local.is_file():
                report.missing.append((line["file"], f"{line['size']} bytes"))
            elif local.stat().st_size != line["size"] or sha256(local) != line["sha256"]:
                report.differ.append((line["file"], f"box {line['size']} bytes, here {local.stat().st_size}"))
            else:
                report.captured += 1
        elif "link" in line:
            report.swept += 1
            local = local_for(line["link"])
            if local.is_symlink() and os.readlink(local) == line["target"]:
                report.captured += 1
            elif local.is_symlink() or local.exists():
                report.differ.append((line["link"], f"a link to {line['target']}"))
            else:
                report.missing.append((line["link"], f"a link to {line['target']}"))
        elif "excluded" in line:
            report.excluded[line["excluded"]] = {"files": line["files"], "bytes": line["bytes"]}
            report.swept += line["files"]
        elif "tree" in line:
            report.trees.append(line)
            if line["recorded"] is None and not on_origin(line["head"]):
                report.problems.append(f"the clone {line['tree']} is at {line['head']}, which origin/main lacks: its "
                                       f"tracked files are not provably elsewhere")
            elif line["recorded"] is not None and line["head"] != line["recorded"]:
                report.problems.append(f"{line['tree']} is at {line['head']}, the repository records "
                                       f"{line['recorded']}")
        elif "unreadable" in line:
            report.problems.append(f"could not read {line['unreadable']}: {line['error']}")
        elif "producing" in line:
            report.problems.append(f"still producing: pid {line['producing']} {line['command']}")
        elif "special" in line:
            report.special.append(f"{line['special']} ({line['mode']})")
        elif "containers" in line:
            report.containers = line["containers"]
        elif "done" in line:
            done = True
    if not done:
        report.problems.append("the sweep was cut short: no done line")
    if report.swept == 0:
        report.problems.append("the sweep found no files: an empty sweep is a failed check")
    return report


def remote_sweep(config: Path, *args: str) -> list[dict]:
    """Run box_sweep.py on the box with the box's python3, the script on ssh's standard input."""
    with open(SWEEP, "rb") as script:
        result = subprocess.run(["ssh", "-F", str(config), "box", "python3", "-", *args], stdin=script,
                                capture_output=True, timeout=SWEEP_TIMEOUT_S)
    lines = [json.loads(text) for text in result.stdout.decode().splitlines() if text.strip()]
    if result.returncode != 0:
        lines.append({"unreadable": "(the sweep)", "error": f"exit {result.returncode}: "
                                                            f"{result.stderr.decode(errors='replace')[-500:]}"})
    return lines


def on_origin(sha: str) -> bool:
    """Whether origin/main, fetched now, holds the commit."""
    if subprocess.run(["git", "-C", str(ROOT), "fetch", "--quiet", "origin"]).returncode != 0:
        return False
    return subprocess.run(["git", "-C", str(ROOT), "merge-base", "--is-ancestor", sha, "origin/main"],
                          capture_output=True).returncode == 0


def read_baseline(instance_id: int) -> frozenset:
    path = box_dir(instance_id) / "baseline.jsonl"
    if not path.exists():
        return frozenset()
    lines = [json.loads(text) for text in path.read_text().splitlines() if text.strip()]
    return frozenset((line["file"], line["size"], line["sha256"]) for line in lines if "file" in line)


def fetch(config: Path, box_paths: list[str], folder: Path) -> None:
    """rsync exactly these box files down: outputs into this repository with --update, the rest under folder/files/."""
    outputs = [rel for rel in map(output_rel, box_paths) if rel]
    others = [path.lstrip("/") for path in box_paths if not output_rel(path)]
    rsh = f"ssh -F {config}"
    for names, source, target, extra in [(outputs, f"box:{REMOTE_REPO}/", ROOT, ["--update"]),
                                         (others, "box:/", folder / "files", [])]:
        if names:
            target.mkdir(parents=True, exist_ok=True)
            subprocess.run(["rsync", "-a", *extra, "--from0", "--files-from=-", "-e", rsh, source, f"{target}/"],
                           input="\0".join(names).encode(), check=False)


def check(instance_id: int, pull: bool) -> dict | None:
    """The capture check; the marker it wrote when it passed, else None."""
    marker = marker_path(instance_id)
    marker.unlink(missing_ok=True)
    config = ssh_config(instance_id)
    folder = box_dir(instance_id)
    baseline = read_baseline(instance_id)

    def sweep_and_compare() -> Report:
        lines = remote_sweep(config, "sweep", REMOTE_REPO, HOME, TMP)
        return compare(lines, lambda path: local_path(path, folder), baseline, on_origin)

    report = sweep_and_compare()
    if pull and report.lost:
        paths = [path for path, _ in report.missing + report.differ]
        print(f"== pulling {len(paths)} files")
        fetch(config, paths, folder)
        report = sweep_and_compare()
    for kind, entries in (("MISSING", report.missing), ("DIFFERS", report.differ)):
        for path, detail in entries[:50]:
            print(f"   {kind}  {path} ({detail})")
    if max(len(report.missing), len(report.differ)) > 50:
        print("   (the first 50 of each)")
    for problem in report.problems:
        print(f"!! {problem}")
    for reason, n in report.excluded.items():
        print(f"   excluded: {n['files']} files, {n['bytes'] / 1e6:.1f} MB: {reason}")
    print(f"   {len(report.special)} special files, {report.containers} running containers, baseline of "
          f"{len(baseline)} host files")
    print(f"== {instance_id}: {report.summary()}")
    if not report.ok:
        print("== the capture check fails: nothing may destroy this box")
        return None
    record = {"id": instance_id, "time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "epoch": time.time(), "swept": report.swept, "captured": report.captured, "image": report.image,
              "lost": report.lost, "excluded": report.excluded, "trees": report.trees,
              "special": len(report.special), "containers": report.containers}
    marker.write_text(json.dumps(record, indent=1) + "\n")
    print(f"== the capture check passes; marker {marker}")
    return record


def baseline(instance_id: int) -> int:
    path = box_dir(instance_id) / "baseline.jsonl"
    if path.exists():
        print(f"== kept the baseline of first contact, {path}")
        return 0
    lines = remote_sweep(ssh_config(instance_id), "baseline", HOME, TMP)
    if not any("done" in line for line in lines) or any("unreadable" in line for line in lines):
        print("!! the baseline sweep failed: " + "; ".join(str(line) for line in lines if "unreadable" in line))
        return 1
    path.write_text("".join(json.dumps(line) + "\n" for line in lines))
    print(f"== baseline: {sum('file' in line for line in lines)} host files, {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["check", "baseline"])
    parser.add_argument("id", type=int)
    parser.add_argument("--pull", action="store_true", help="fetch what is missing or different, then sweep again")
    args = parser.parse_args(argv)
    try:
        if args.command == "baseline":
            return baseline(args.id)
        return 0 if check(args.id, args.pull) else 1
    except (FileNotFoundError, ValueError, subprocess.TimeoutExpired) as error:
        print(f"!! {error}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
