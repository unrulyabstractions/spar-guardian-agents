"""What a box is doing: its launcher and cells by their command lines in /proc, and the newest write among its runs.

    ssh -F logs/boxes/<id>/ssh_config box python3 - probe --key < scripts/box/box_probe.py   # one JSON line
    ssh -F logs/boxes/<id>/ssh_config box python3 - status < scripts/box/box_probe.py        # lines for watch.sh
    python3 scripts/box/box_probe.py verdict <id> < reading.json                              # the guard's line

probe runs on the box, on its own python3 with the standard library alone, and reads only. It prints the id in
/root/box_id, whether the clone exists, the launcher's pids (`python -m scripts.study.run_study`) and its start, each
live cell by its run name (`-m studies.cells.<...>` or `-m examples.<...>` with `--name <run>`, uv's process and
Python's counting once), the newest write among the files under the clone's runs/ and logs/ and Inspect's traces and
sample buffers, the time on the box, the runs stored under each study (a run.json), and, with --key, what the box's own
key has left at OpenRouter (nokey, err, inf or dollars). The launcher's own output and plan are not writes of the runs.

verdict runs on the Mac and turns a reading into the line .claude/box_progress.sh prints for the cloud box guard:
    unreachable wrong-box <name>   /root/box_id names another box: nothing may say `ended` of this id
    ended no-clone-on-the-box      box_setup.sh has not cloned yet (the guard's setup grace covers it)
    blocked <why>                  the box's key has no limit left: no run can progress
    ended launcher-ended           no launcher and no cell lives: the box is idle
    progress <seconds>             a launcher or a cell lives, and this long ago was the last write, or the launcher's
                                   start when nothing was written since
A launcher that waits on its queue and writes nothing for long reads as progress of a growing age, which the guard
counts as idle. Cells that outlive their launcher are progress.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.request

REPO, HOME, PROC = "/root/work", "/root", "/proc"
INSPECT = ".local/share/inspect_ai"
"""Inspect's data folder under the home: its traces and sample buffers grow while a run works."""
LAUNCHER = "scripts.study.run_study"
CELLS = ("studies.cells.", "examples.")
KEY_URL = "https://openrouter.ai/api/v1/key"
KEY_LINE = re.compile(r"\s*(?:export\s+)?[A-Z_]*OPENROUTER_API_KEY=['\"]?([^'\"\s]+)")


def command_line(proc: str, pid: str) -> list[str] | None:
    try:
        with open(f"{proc}/{pid}/cmdline", "rb") as handle:
            return [part.decode(errors="replace") for part in handle.read().split(b"\0") if part]
    except OSError:
        return None


def module_of(args: list[str]) -> str:
    """The module a `python -m <module>` command line runs, or ""."""
    return args[args.index("-m") + 1] if "-m" in args[:-1] else ""


def processes(proc: str = PROC) -> tuple[list[int], dict[str, int]]:
    """(the launcher's pids, {run name: pid} of each live cell). This process never counts."""
    launchers, cells = [], {}
    for pid in sorted(filter(str.isdigit, os.listdir(proc)), key=int):
        args = command_line(proc, pid)
        if not args or int(pid) == os.getpid():
            continue
        module = module_of(args)
        if module == LAUNCHER:
            launchers.append(int(pid))
        elif module.startswith(CELLS) and "--name" in args[:-1]:
            cells.setdefault(args[args.index("--name") + 1], int(pid))
    return launchers, cells


def started(proc: str, pid: int) -> float | None:
    """When the process started, from its stat's start time and the kernel's boot time; None when unreadable."""
    try:
        with open(f"{proc}/{pid}/stat") as handle:
            ticks = int(handle.read().rsplit(")", 1)[1].split()[19])
        with open(f"{proc}/stat") as handle:
            boot = next(int(line.split()[1]) for line in handle if line.startswith("btime "))
    except (OSError, IndexError, ValueError, StopIteration):
        return None
    return boot + ticks / os.sysconf("SC_CLK_TCK")


def newest_write(roots: list[str]) -> float | None:
    """The newest modification time among the files under the roots; None when they hold none."""
    newest = None
    for root in roots:
        for dirpath, _, filenames in os.walk(root):
            for name in filenames:
                try:
                    mtime = os.lstat(os.path.join(dirpath, name)).st_mtime
                except OSError:
                    continue
                newest = mtime if newest is None else max(newest, mtime)
    return newest


def stored_runs(repo: str) -> dict[str, int]:
    """{study: the runs it stores at its top level}, a run being a folder with processed/run.json."""
    runs = os.path.join(repo, "runs")
    out = {}
    for study in sorted(os.listdir(runs)) if os.path.isdir(runs) else []:
        folder = os.path.join(runs, study)
        if os.path.isdir(folder):
            out[study] = sum(os.path.isfile(os.path.join(folder, name, "processed", "run.json"))
                             for name in os.listdir(folder))
    return out


def key_left(box_env: str, opener=urllib.request.urlopen) -> str | float:
    """What the box's own key has left at OpenRouter: dollars, inf for no limit, nokey or err."""
    key = None
    try:
        with open(box_env) as handle:
            key = next((match[1] for match in map(KEY_LINE.match, handle) if match), None)
    except OSError:
        pass
    if not key:
        return "nokey"
    try:
        request = urllib.request.Request(KEY_URL, headers={"Authorization": "Bearer " + key})
        with opener(request, timeout=20) as response:
            left = json.load(response)["data"].get("limit_remaining")
    except Exception:  # noqa: BLE001 - any failure to read the key is no reading
        return "err"
    return "inf" if left is None else float(left)


def probe(repo: str = REPO, home: str = HOME, proc: str = PROC, key: bool = False) -> dict:
    """One reading of the box."""
    try:
        with open(os.path.join(home, "box_id")) as handle:
            box_id = handle.read().strip()
    except OSError:
        box_id = None
    launchers, cells = processes(proc)
    starts = [start for start in (started(proc, pid) for pid in launchers) if start is not None]
    inspect = os.path.join(home, INSPECT)
    roots = [os.path.join(repo, "runs"), os.path.join(repo, "logs"), os.path.join(inspect, "traces"),
             os.path.join(inspect, "samplebuffer")]
    return {"box_id": box_id, "work": os.path.isdir(repo), "launcher": launchers,
            "launcher_started": min(starts) if starts else None, "cells": cells, "newest": newest_write(roots),
            "now": time.time(), "stored": stored_runs(repo),
            "key": key_left(os.path.join(home, ".box_env")) if key else "unread"}


def verdict(reading: dict, want: str) -> str:
    """The guard's line for one reading of box `want`."""
    if reading.get("box_id") != want:
        return f"unreachable wrong-box {re.sub(r'[^A-Za-z0-9._-]', '', str(reading.get('box_id')))[:40] or 'none'}"
    if not reading["work"]:
        return "ended no-clone-on-the-box"
    if isinstance(reading["key"], (int, float)) and reading["key"] <= 0:
        return "blocked the-box-key-has-no-limit-left"
    if not reading["launcher"] and not reading["cells"]:
        return "ended launcher-ended"
    since = max(filter(None, (reading["newest"], reading["launcher_started"])), default=reading["now"])
    return f"progress {max(0, int(reading['now'] - since))}"


def status(reading: dict) -> list[str]:
    """watch.sh's lines: the launcher, the live cells, the last write and the runs stored, then `all runs ended` once
    no launcher and no cell lives."""
    launcher = f"launcher alive (pid {', '.join(map(str, reading['launcher']))})" if reading["launcher"] \
        else "launcher ended"
    ago = f"{(reading['now'] - reading['newest']) / 60:.0f} min ago" if reading["newest"] else "never"
    stored = ", ".join(f"{study} {n}" for study, n in reading["stored"].items()) or "none"
    out = [f"{launcher}; {len(reading['cells'])} cells running; last write {ago}; runs stored: {stored}"]
    out += [f"   running {name} (pid {pid})" for name, pid in sorted(reading["cells"].items())]
    if not reading["launcher"] and not reading["cells"]:
        out.append("all runs ended: capture this box and destroy it")
    return out


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[0] == "verdict":
        print(verdict(json.loads(sys.stdin.read()), argv[1]))
        return 0
    if argv[:1] in (["probe"], ["status"]) and argv[1:] in ([], ["--key"]):
        reading = probe(key=argv[1:] == ["--key"])
        print(json.dumps(reading) if argv[0] == "probe" else "\n".join(status(reading)))
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
