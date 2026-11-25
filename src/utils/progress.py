"""The terminal output of the make targets: a heading per step, indented details, a counter for long loops and a
one-line summary at the end. Standard library only, so `python3 -m src.utils.progress` runs before .venv exists.

    from src.utils import progress
    progress.step("Index the runs")                 # a blank line, then the heading
    progress.detail("cache", "tmp/index.json")      # "  cache        tmp/index.json"
    with progress.Counter(258, "measured", "runs") as count:
        for run in runs:
            count.tick()                            # "  measured 120 of 258 runs (47%), about 1 min left"
    progress.wrote("tmp/index.json")                # "  wrote        tmp/index.json"
    progress.verbose("counts", counts)              # only with VERBOSE=1: the numbers behind an output
    progress.done("indexed 258 runs", out="tmp/")   # "Done in 1 min 12 s: indexed 258 runs; output in tmp/"

On a terminal the counter rewrites its line in place; elsewhere (a pipe, a log file) it prints a plain line at every
tenth of the way and at the end. Plain text only: no colour, no emoji.

The module is also the make targets' step runner, which runs commands one after another, each under a numbered
heading with its output indented, stops at the first failure and ends with the summary line:

    python3 -m src.utils.progress "Results tables" --out tmp/tables/results \\
        --py scripts.paper.control_tables --step "the cheating table" "uv run python -m scripts.paper.cheating_table"

A command runs on a pseudo-terminal when the runner's output is a terminal, so its own counters and colours still
work. The runner sets PROGRESS_NESTED=1 for its commands; a nested script's done() prints an indented line, since the
runner prints the summary.
"""
from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
import time

INDENT = "  "
LABEL = 12
"""The width of a detail's label, so the values line up."""
NESTED = "PROGRESS_NESTED"
"""The environment variable the step runner sets for its commands."""


def _process_start() -> float:
    """When this process started, on time.monotonic()'s clock: its age from ps, so the imports before this module
    count; the import time of this module where ps cannot tell."""
    now = time.monotonic()
    try:
        etime = subprocess.run(["ps", "-o", "etime=", "-p", str(os.getpid())], capture_output=True, text=True,
                               timeout=2).stdout.strip()
        days, _, clock = etime.rpartition("-")
        seconds = 0
        for part in clock.split(":"):
            seconds = seconds * 60 + int(part)
        return now - seconds - 86400 * int(days or 0)
    except (OSError, ValueError, subprocess.SubprocessError):
        return now


START = _process_start()
_printed = False
_in_step = False


def is_terminal(stream=None) -> bool:
    """Whether `stream` (stdout by default) is an interactive terminal that can rewrite a line in place."""
    stream = stream or sys.stdout
    try:
        return stream.isatty() and os.environ.get("TERM") != "dumb"
    except (AttributeError, ValueError):
        return False


def nested() -> bool:
    """Whether this process runs as one step of the step runner."""
    return os.environ.get(NESTED) == "1"


def duration(seconds: float) -> str:
    """A span in plain words: 0.4 s, 12 s, 3 min 5 s, 1 h 4 min."""
    if seconds < 10:
        return f"{seconds:.1f} s"
    seconds = round(seconds)
    if seconds < 60:
        return f"{seconds} s"
    if seconds < 3600:
        return f"{seconds // 60} min {seconds % 60} s" if seconds % 60 else f"{seconds // 60} min"
    return f"{seconds // 3600} h {seconds % 3600 // 60} min"


def remaining(seconds: float) -> str:
    """An estimate of the time left, rounded as an estimate deserves: about 20 s, about 3 min, about 1 h 10 min."""
    if seconds < 60:
        return f"about {max(5, round(seconds / 5) * 5)} s"
    if seconds < 3600:
        return f"about {round(seconds / 60)} min"
    return f"about {int(seconds // 3600)} h {round(seconds % 3600 / 60)} min"


def _emit(line: str, err: bool = False) -> None:
    global _printed
    print(line, file=sys.stderr if err else sys.stdout, flush=True)
    _printed = True


def step(title: str) -> None:
    """Start a step: a blank line after any earlier output, then the heading. Details after it are indented."""
    global _in_step
    if _printed:
        _emit("")
    _emit(title)
    _in_step = True


def detail(label: str, value: object = None, err: bool = False) -> None:
    """One line under the current step: `label` alone, or `label` and `value` in two aligned columns. A value of
    several lines keeps each further line under the first."""
    pad = INDENT if _in_step else ""
    if value is None:
        _emit(pad + label, err)
        return
    lines = str(value).splitlines() or [""]
    head = f"{label:<{LABEL}} " if label else " " * (LABEL + 1)
    _emit(pad + head + lines[0], err)
    for line in lines[1:]:
        _emit(pad + " " * (LABEL + 1) + line, err)


def plural(n: int, noun: str, many: str | None = None) -> str:
    """`n` and its noun: "1 run", "3 runs", "1 study", "2 studies" (`many` when not the noun and an s)."""
    return f"{n} {noun if n == 1 else many or noun + 's'}"


def short(path: object) -> str:
    """`path` relative to the working directory when under it, for shorter lines."""
    text = str(path)
    try:
        return os.path.relpath(text) if os.path.abspath(text).startswith(os.getcwd() + os.sep) else text
    except ValueError:
        return text


def wrote(path: object, what: str = "wrote") -> None:
    """A detail naming a file written: "  wrote        tmp/tables/x.png"."""
    detail(what, short(path))


def verbose(*parts: object) -> None:
    """A detail printed only with VERBOSE=1 in the environment (make <target> VERBOSE=1): the numbers behind a table
    or a figure, for checking it by eye."""
    if os.environ.get("VERBOSE"):
        detail(" ".join(str(p) for p in parts))


def done(summary: str, out: object = None, word: str = "Done", started: float | None = None) -> None:
    """The last line, "Done in 12 s: what was done; output in <out>", timed from `started` (the process's start, to
    the second, by default). Under the step runner it is an indented detail instead, since the runner ends with its
    own."""
    global _in_step
    took = duration(time.monotonic() - (START if started is None else started))
    where = f"; output in {out}" if out else ""
    if nested():
        detail(f"{word.lower()} in {took}: {summary}{where}")
        return
    if _printed:
        _emit("")
    _emit(f"{word} in {took}: {summary}{where}")
    _in_step = False


def failed(summary: str, started: float | None = None) -> None:
    """The last line of a failure, on stderr, "Failed after 3 s: what failed"."""
    took = duration(time.monotonic() - (START if started is None else started))
    if _printed and not nested():
        _emit("", err=True)
    _emit(f"{'' if not nested() else INDENT}Failed after {took}: {summary}", err=True)


class Counter:
    """How far a loop of `total` items has gone, as "measured 120 of 258 runs (47%), about 1 min left". On a terminal
    the line is rewritten in place at most every `interval` seconds; elsewhere a plain line is printed at every tenth
    of the way. close() (or the end of a with block) prints the final count with the time taken."""

    def __init__(self, total: int, verb: str, noun: str, interval: float = 0.2):
        self.total, self.verb, self.noun, self.interval = total, verb, noun, interval
        self.n = 0
        self.started = time.monotonic()
        self.live = is_terminal()
        self._drawn = 0.0
        self._shown = False
        self._tenth = 0
        self._first: tuple[int, float] | None = None
        self.note = ""

    def __enter__(self) -> Counter:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def text(self) -> str:
        """The current count, its share and the estimate of the time left."""
        pct = 100 * self.n // self.total if self.total else 100
        line = f"{self.verb} {self.n} of {self.total} {self.noun} ({pct}%)"
        if self._first and self.n < self.total:  # the rate since the first item, so a slow start skews nothing
            n0, t0 = self._first
            spent = time.monotonic() - t0
            if self.n - n0 >= 2 and spent > 0.5:
                line += f", {remaining(spent / (self.n - n0) * (self.total - self.n))} left"
        return line + (f"  {self.note}" if self.note else "")

    def _draw(self) -> None:
        global _printed
        pad = INDENT if _in_step else ""
        line = pad + self.text()
        width = _width()
        if len(line) > width - 1:
            line = line[:width - 2] + "~"
        sys.stdout.write("\r\x1b[K" + line)
        sys.stdout.flush()
        self._shown = True
        _printed = True

    def tick(self, n: int = 1, note: str = "") -> None:
        """Count `n` more items done; `note` names the latest one on a terminal."""
        self.n += n
        self.note = note
        if self._first is None:
            self._first = (self.n, time.monotonic())
        if self.live:
            now = time.monotonic()
            if now - self._drawn >= self.interval or self.n >= self.total:
                self._drawn = now
                self._draw()
        elif self.total and self.n < self.total and self.n * 10 // self.total > self._tenth:
            self._tenth = self.n * 10 // self.total
            self.note = ""
            detail(self.text())

    def log(self, line: str, err: bool = False) -> None:
        """A line printed above the counter, which is drawn again under it on a terminal."""
        if self.live and self._shown:
            sys.stdout.write("\r\x1b[K")
            sys.stdout.flush()
        detail(line, err=err)
        if self.live and self._shown:
            self._draw()

    def close(self) -> None:
        """The final line: the count and the time it took."""
        if self.live and self._shown:
            sys.stdout.write("\r\x1b[K")
            sys.stdout.flush()
        self._shown = False
        self.live = False
        detail(f"{self.verb} {self.n} of {self.total} {self.noun} in {duration(time.monotonic() - self.started)}")


def _width() -> int:
    """The terminal's width; 120 when it reports none (a pseudo-terminal with no size reports 0)."""
    try:
        columns = os.get_terminal_size(sys.stdout.fileno()).columns
    except (OSError, ValueError):
        columns = 0
    return columns if columns >= 40 else 120


# ------------------------------------------------------------------------------------------------ the step runner

def _indented(chunk: bytes, at_line_start: bool) -> tuple[bytes, bool]:
    """`chunk` with INDENT after every line start (a newline or a carriage return), and whether it ends at one."""
    out = bytearray()
    for byte in chunk:
        if at_line_start and byte not in (10, 13):
            out += INDENT.encode()
            at_line_start = False
        out.append(byte)
        if byte in (10, 13):
            at_line_start = True
    return bytes(out), at_line_start


def _run_on_terminal(command: str, env: dict) -> int:
    """Run `command` on a pseudo-terminal the width of ours less the indent, copying its output indented."""
    import fcntl
    import pty
    import struct
    import termios

    leader, follower = pty.openpty()
    try:
        rows, cols = os.get_terminal_size(sys.stdout.fileno())
        if cols >= 40:  # a terminal with no size reports 0; the command then keeps its own default
            fcntl.ioctl(follower, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols - len(INDENT), 0, 0))
    except OSError:
        pass
    proc = subprocess.Popen(command, shell=True, stdin=subprocess.DEVNULL, stdout=follower, stderr=follower, env=env)
    os.close(follower)
    start = True
    while True:
        try:
            chunk = os.read(leader, 65536)
        except OSError:  # the command and its children closed the terminal
            break
        if not chunk:
            break
        text, start = _indented(chunk, start)
        sys.stdout.buffer.write(text)
        sys.stdout.flush()
    os.close(leader)
    return proc.wait()


def _run_on_pipe(command: str, env: dict) -> int:
    """Run `command` with its output and errors on one pipe, copied indented."""
    proc = subprocess.Popen(command, shell=True, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, env=env)
    start = True
    for chunk in iter(lambda: proc.stdout.read1(65536), b""):
        text, start = _indented(chunk, start)
        sys.stdout.buffer.write(text)
        sys.stdout.flush()
    return proc.wait()


def run_command(command: str) -> int:
    """Run one shell command as a step, its output indented under the step's heading; its exit code."""
    env = {**os.environ, NESTED: "1"}
    sys.stdout.flush()
    if is_terminal() and os.name == "posix":
        code = _run_on_terminal(command, env)
    else:
        code = _run_on_pipe(command, env)
    return code


def run_steps(title: str, steps: list[tuple[str, str]], out: str | None = None, summary: str | None = None) -> int:
    """Run each (label, shell command) in order under a numbered heading (one step has its title alone), stop at the
    first failure, and end with the summary line (`summary`, else the title and the steps run). Returns the failing
    command's exit code, else 0. With `out` a folder, the summary counts the files the steps wrote under it."""
    started, wall = time.monotonic(), time.time()
    many = len(steps) > 1
    if many:
        _emit(f"{title}: {len(steps)} steps")
    for i, (label, command) in enumerate(steps, 1):
        step(f"[{i}/{len(steps)}] {label}" if many else title)
        t0 = time.monotonic()
        code = run_command(command)
        if code:
            which = f"step {i} of {len(steps)} ({label})" if many else label
            failed(f"{title.lower() if many else title}, {which} exited {code}", started)
            return code
        if many:
            detail(f"took {duration(time.monotonic() - t0)}")
    what = summary or (f"{title}, {len(steps)} of {len(steps)} steps" if many else title)
    written = files_written(out, wall) if out else None
    if written is not None:
        what += f", {written} file{'' if written == 1 else 's'} written"
    done(what, out=out, started=started)
    return 0


def files_written(folder: str, since: float) -> int | None:
    """How many files under `folder` changed at or after `since` (a wall-clock time); None when it is no folder."""
    if not os.path.isdir(folder):
        return None
    return sum(1 for top, _, names in os.walk(folder) for name in names
               if os.path.getmtime(os.path.join(top, name)) >= since - 1)


def _label(module: str) -> str:
    return module.rsplit(".", 1)[-1].replace("_", " ")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m src.utils.progress",
                                 description="Run commands as numbered steps with indented output and a summary.")
    ap.add_argument("title", help="what the steps make, as the summary names it")
    ap.add_argument("--out", help="where the output goes, for the summary line")
    ap.add_argument("--summary", help="what the summary line says was done (the title and the steps by default)")
    ap.add_argument("--python", default="uv run python", help="the interpreter --py steps run under")
    ap.add_argument("--step", nargs=2, action="append", metavar=("LABEL", "COMMAND"), dest="steps", default=[],
                    type=str, help="a step: its label and a shell command")
    ap.add_argument("--py", action="append", metavar="MODULE", dest="steps", type=str,
                    help="a step that runs a Python module (with its arguments), labelled by the module's name")
    args = ap.parse_args(argv)
    steps = []
    for item in args.steps:
        if isinstance(item, list):
            steps.append((item[0], item[1]))
        else:
            module = shlex.split(item)[0]
            steps.append((_label(module), f"{args.python} -m {item}"))
    if not steps:
        ap.error("give at least one --step or --py")
    return run_steps(args.title, steps, args.out, args.summary)


if __name__ == "__main__":
    raise SystemExit(main())
