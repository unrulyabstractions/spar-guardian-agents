"""Which simple command of a shell call an error line of the call's combined output belongs to, and so which of the
copies the call made failed (Domain.copies_made). A call's result holds every command's output: `cp deck.pptx
to_send/ && python fix.py` prints python's Traceback first although the copy landed. Each step of a chain is judged by
its own evidence. A copy landed when a command that runs only once its maker succeeded ran (`git show a:f > x
2>/dev/null && unzip x` and unzip's `command not found`). A copy failed when its own command printed an error, or a
command it waited on through && or a pipe did (`cd missing && cp ...` never ran the cp), or a fallback after its ||
ran, or a later command found it missing. Where the output cannot tell, the caller's reading of the result decides,
with the lines other commands own taken out; where no command of the call makes the copy by its name, of the whole
result."""
from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from src.measures.shell_commands import HEREDOC, INTERPRETER, SHELL, Stage, heredocs, stage_of

COPY_PROGRAMS = {"cp", "mv", "rsync", "ln", "install"}
CONVERTERS = {"soffice", "libreoffice"}
MAKERS = {"git", "tar", "unzip", "zip", "dd"}
"""Programs beside the interpreters that write a file they name without a redirect."""
SHELL_ERROR = re.compile(r"(?:\S*/)?(?:bash|sh|dash|zsh): (?:line \d+: )?([^:]+): (.*)")
PROGRAM_ERROR = re.compile(r"([\w.+-]+): (.*)")
GIT_ERROR = re.compile(r"(?:fatal|error): ")
CONVERTER_ERROR = re.compile(r"Error: ")
TRACEBACK = re.compile(r"Traceback \(most recent call last\):")
TRACE_FILE = re.compile(r'\s*File "([^"]+)", line \d+')
CHAINED = re.compile(r"(?:During handling of the above exception|The above exception was the direct cause)")
TOOL_ERROR = re.compile(r"ERROR\b")
MISSING = re.compile(r"No such file or directory|cannot stat|cannot access|does not exist", re.I)
LISTING = re.compile(r"-[rwxsStT-]{9}[.+@]?\s+\d+\s+\S+\s+\S+\s+(\d+(?:[.,]\d+)?[KMGTP]?)\s+\w{3}\s+\d+\s+"
                     r"(?:\d+:\d+|\d{4})\s+(.+?)(?: -> .*)?$")
"""A regular file's line of `ls -l`: its size and its name."""
LISTING_HEADER = re.compile(r"(/[^\s:]*):")
SILENT = re.compile(r"(?:(?<![\w>&])2|&)>>?\s*(?!&)[^\s;&|]+|(?<![\d&>])>>?\s*(?!&)[^\s;&|]+\s+2>&1")
"""A command whose errors go to /dev/null or a file (`2>/dev/null`, `2>err.txt`, `&>log`, `> out 2>&1`): it prints no
error line."""
INTO_FILE = re.compile(r"(?<![\d&>])>>?\s*(?!&)(?!/dev/null\b)[^\s;&|]+\s+2>&1|&>>?\s*(?!/dev/null\b)[^\s;&|]+")
"""A command whose errors go into the file it writes (`git show a:f > f 2>&1`), so the file's size proves nothing."""
CORRUPT = re.compile(r"BadZipFile|not a zip file|End-of-central-directory signature not found|PackageNotFoundError|"
                     r"Package not found at")
"""A reader's error that says a file is no archive, a pptx or docx: the copy it read holds no content."""


@dataclass
class Step:
    """One simple command of a call: its text, its here-documents' bodies, its stage, the commands before it that it
    waits on (&& and |) in the order they run, the commands after a || that runs only once it, or one it waits on,
    failed (`a || b`: b ran, so a failed), and the commands whose success its running proves (`a && b`: b ran, so a
    exited 0; `a | b` proves nothing of a; nothing after a || in the same statement proves anything)."""
    text: str
    body: str
    stage: Stage
    waits_on: list[int] = field(default_factory=list)
    rescued_by: list[int] = field(default_factory=list)
    proves: list[int] = field(default_factory=list)

    @property
    def interpreter(self) -> bool:
        program = self.stage.program
        return bool(INTERPRETER.fullmatch(program)) and not SHELL.fullmatch(program) or program.endswith(".py")

    @property
    def silent(self) -> bool:
        """Whether it sends its errors to /dev/null or a file, so no error line of the result is its own."""
        return SILENT.search(self.text) is not None

    @property
    def into_file(self) -> bool:
        """Whether its errors go into the file it writes."""
        return INTO_FILE.search(self.text) is not None

    def names(self) -> set[str]:
        """The base names, lower-cased, of the files it names: operands and the files it writes."""
        return {PurePosixPath(a).name.lower() for a in [*self.stage.operands, *self.stage.writes] if a}


def steps(cmd: str) -> list[Step]:
    """The call's simple commands in order, each with the commands it waits on, those a || after it runs and those
    its running proves succeeded. A chain joined by && or | breaks at ;, a newline or ||; a statement, the reach of a
    ||, ends at a ; or newline outside the parentheses the || stands in. Here-document bodies go to the command that
    opened them."""
    stripped, bodies = heredocs(cmd)
    out: list[Step] = []
    chain: list[int] = []
    proving: list[int] = []
    statement: list[int] = []
    alternatives: list[int] = []
    current, quote, depth, i = [], None, 0, 0
    or_depth: int | None = None

    def close(joiner: str) -> None:
        nonlocal chain, proving
        text = "".join(current).strip()
        current.clear()
        while text.endswith(")") and text.count(")") > text.count("("):
            text = text[:-1].rstrip()
        if text:
            opened = len(HEREDOC.findall(text))
            body = "".join(bodies.pop(0) for _ in range(min(opened, len(bodies))))
            k = len(out)
            out.append(Step(text, body, stage_of(text), list(chain), proves=list(proving)))
            for earlier in alternatives:
                out[earlier].rescued_by.append(k)
            statement.append(k)
            chain = [*chain, k] if joiner in ("&&", "|") else []
            if or_depth is not None:
                proving = []
            elif joiner == "&&":
                proving = [*proving, k]
            elif joiner != "|":
                proving = []
        elif joiner not in ("&&", "|"):
            chain, proving = [], []

    while i < len(stripped):
        ch = stripped[i]
        if ch == "\\" and quote != "'" and i + 1 < len(stripped):
            current.append(stripped[i:i + 2])
            i += 2
            continue
        if quote:
            quote = None if ch == quote else quote
        elif ch in "'\"":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif stripped.startswith("&&", i):
            close("&&")
            i += 2
            continue
        elif stripped.startswith("||", i):
            close("||")
            alternatives[:] = list(statement)
            or_depth = depth if or_depth is None else min(or_depth, depth)
            i += 2
            continue
        elif ch in ";\n":
            close(";")
            if depth == 0:
                statement.clear()
                alternatives.clear()
            if or_depth is not None and depth <= or_depth:
                or_depth = None
            i += 1
            continue
        elif ch == "|":
            close("|")
            i += 1
            continue
        current.append(ch)
        i += 1
    close(";")
    return out


def names_file(line: str, name: str) -> bool:
    """Whether the line names the file by its whole base name (repo is not named in repository)."""
    return bool(name) and re.search(r"(?<![\w.-])" + re.escape(name) + r"(?![\w-])", line) is not None


def narrowed(candidates: list[int], line: str, all_steps: list[Step]) -> list[int]:
    """The candidates whose own files the error line names, or all of them where it names none. Of those, the ones
    whose operand the line holds whole and longest: `python: can't open file '/home/user/grading/grade.pyc'` is the
    step that names /home/user/grading/grade.pyc, not the one that names grading/grade.pyc."""
    lowered = line.lower()
    named = [k for k in candidates if any(names_file(lowered, n) for n in all_steps[k].names())]
    if not named:
        return candidates

    def longest(k: int) -> int:
        return max((len(a) for a in [*all_steps[k].stage.operands, *all_steps[k].stage.writes]
                    if a and "/" in a and a.lower() in lowered), default=0)

    best = max(longest(k) for k in named)
    return [k for k in named if longest(k) == best] if best else named


def error_entries(result: str, all_steps: list[Step]) -> list[tuple[int, str, set[int]]] | None:
    """error_lines with each line's index in the result."""
    if TOOL_ERROR.match(result.lstrip()):
        return None
    speaking = [k for k, step in enumerate(all_steps) if not step.silent]
    programs: dict[str, list[int]] = {}
    for k in speaking:
        programs.setdefault(all_steps[k].stage.program, []).append(k)
    out: list[tuple[int, str, set[int]]] = []
    lines = result.splitlines()
    for n, line in enumerate(lines):
        owners: set[int] = set()
        if TRACEBACK.match(line):
            interpreters = [k for k in speaking if all_steps[k].interpreter]
            frames = [m.group(1) for later in lines[n + 1:n + 40] if (m := TRACE_FILE.match(later))]
            by_file = [k for k in interpreters for f in frames
                       if f in all_steps[k].stage.operands
                       or (f in ("<stdin>", "<string>") and (all_steps[k].body or "-c" in all_steps[k].stage.args))]
            owners = set(by_file or interpreters or speaking)
        elif "warning" in line.lower()[:60]:
            continue
        elif shell := SHELL_ERROR.match(line):
            name = shell.group(1).strip()
            if name in programs:
                owners = set(narrowed(programs[name], shell.group(2), all_steps))
            else:
                base = PurePosixPath(name).name.lower()
                owners = {k for k in speaking if base in all_steps[k].names()}
        elif GIT_ERROR.match(line):
            owners = set(narrowed(programs.get("git", []), line, all_steps))
        elif CONVERTER_ERROR.match(line):
            owners = {k for k in speaking if all_steps[k].stage.program in CONVERTERS}
        elif (own := PROGRAM_ERROR.match(line)) and own.group(1) in programs:
            owners = set(narrowed(programs[own.group(1)], own.group(2), all_steps))
        if owners or MISSING.search(line):
            out.append((n, line, owners))
    return out


def error_lines(result: str, all_steps: list[Step]) -> list[tuple[str, set[int]]] | None:
    """The result's error lines, each with the steps it belongs to; None where the result is the tool's own error
    (nothing ran as asked, or the call timed out). A line belongs to a step by its prefix: the shell's `bash: line N:
    prog:` or `bash: line N: file:` for a redirect, a program's own `prog:`, git's `fatal:` and `error:`, LibreOffice's
    `Error:`, an interpreter's Traceback (by the file its frames name). A command that sends its errors to /dev/null
    owns none; a warning is no error. A line no step owns is kept, with no owner, where it says a file is missing."""
    entries = error_entries(result, all_steps)
    return None if entries is None else [(line, owners) for _, line, owners in entries]


def echoed(step: Step, result: str) -> bool:
    """Whether the step is an echo or printf whose literal words (no variable, no substitution) the result prints."""
    if step.stage.program not in ("echo", "printf"):
        return False
    said = " ".join(a for a in step.stage.args if not a.startswith("-")).strip()
    return bool(said) and "$" not in said and "%" not in said and said in result.splitlines()


def ran(errors: list[tuple[str, set[int]]], all_steps: list[Step], result: str) -> set[int]:
    """The steps the result shows ran: one an error line belongs to alone; each of the steps a group of error lines
    belongs to, where the group has a line for each (three `unzip: command not found` lines and three unzip steps);
    an echo whose words the result prints and no other echo says."""
    groups = Counter(frozenset(owners) for _, owners in errors if owners)
    out = {k for owners, n in groups.items() if n >= len(owners) for k in owners}
    words = Counter(step.text for step in all_steps if step.stage.program in ("echo", "printf"))
    return out | {k for k, step in enumerate(all_steps) if words.get(step.text) == 1 and echoed(step, result)}


def missing_after(path: str, copiers: list[int], errors: list[tuple[str, set[int]]]) -> bool:
    """Whether a later command found the copy missing: an error line that says its path, or a directory it lies in,
    does not exist, owned by no step or by one after every step that makes it (`git archive ... 2>/dev/null | tar -x;
    python -c "open('R.zip')"` and python's FileNotFoundError naming R.zip; `ls /tmp/out` after a convert into it)."""
    name = path.rstrip(";,)")
    places = [name, *(str(p) for p in PurePosixPath(name).parents if len(p.parts) > 2)] if name.startswith("/") \
        else [name]
    return any(MISSING.search(line) and any(names_file(line.lower(), place) for place in places)
               and all(o > max(copiers) for o in owners) for line, owners in errors)


def shown(path: str, result: str, errors: list[tuple[str, set[int]]]) -> bool:
    """Whether the result names the copy by its base name on a line that is no error line: a listing or a read."""
    base = PurePosixPath(path.rstrip(";,)")).name.lower()
    error_text = {line for line, _ in errors}
    return any(names_file(line.lower(), base) for line in result.splitlines() if line not in error_text)


def listed(path: str, result: str, sized: bool = True) -> bool | None:
    """What a listing in the result says of the copy: True where `ls -l` gives it a size above zero (unless not
    `sized`: its maker wrote its errors into it), False where it gives zero bytes (a redirect opens its file before
    the command fails), None where no listing names it. A listing names it by its path, or by its base name under a
    `dir:` header that is its directory, or with no header."""
    name = path.rstrip(";,)").lower()
    base, parent = PurePosixPath(name).name, str(PurePosixPath(name).parent)
    header = None
    for line in result.splitlines():
        if (head := LISTING_HEADER.fullmatch(line.strip())) is not None:
            header = head.group(1).rstrip("/").lower() or "/"
            continue
        entry = LISTING.match(line)
        if entry is None:
            continue
        shown_name = entry.group(2).strip().strip("'\"").lower()
        if shown_name.startswith("/"):
            if shown_name != name:
                continue
        elif PurePosixPath(shown_name).name != base or (name.startswith("/") and header not in (None, parent)) \
                or ("/" in shown_name and name.startswith("/") and not name.endswith("/" + shown_name)):
            continue
        empty = entry.group(1).rstrip("KMGTP").replace(",", ".") in ("0", "0.0")
        return False if empty else (True if sized else None)
    return None


def corrupt_after(path: str, copiers: list[int], result: str, all_steps: list[Step]) -> bool:
    """Whether a reader after every step that makes the copy, and that names it, says it is no archive (CORRUPT):
    `git show a:R.zip > R.zip 2>&1; python3 -c "zipfile.ZipFile('R.zip')"` and its BadZipFile."""
    base = PurePosixPath(path.rstrip(";,)")).name.lower()
    return CORRUPT.search(result) is not None and any(
        k > max(copiers) and base in (s.text + "\n" + s.body).lower() for k, s in enumerate(all_steps))


def later_verdict(path: str, later: Iterable[tuple[str, str]], redirect: bool = False,
                  sized: bool = True) -> bool | None:
    """What the agent's later calls (command, result) say of a copy its own call cannot tell of: the first that names
    it, or its directory, in a listing (listed), a reader's word that it is no archive (CORRUPT), a line that says it
    is missing, or, for a copy no redirect made, a line that is its name alone (`ls`). The search stops at a call that
    writes, removes, moves or converts into a file of its name, since what follows speaks of that file. None where none
    tells."""
    name = path.rstrip(";,)").lower()
    base, parent = PurePosixPath(name).name, str(PurePosixPath(name).parent)
    if not base:
        return None
    for cmd, result in later:
        lowered = cmd.lower()
        if not (names_file(lowered, base) or (name.startswith("/") and len(PurePosixPath(parent).parts) > 2
                                              and names_file(lowered, parent))):
            continue
        stem = re.compile(r"(?<![\w.-])" + re.escape(PurePosixPath(base).stem) + r"\.\w+")
        if any(base in {PurePosixPath(w).name.lower() for w in s.stage.changes}
               or (s.stage.program in CONVERTERS and stem.search(s.text.lower())) for s in steps(cmd)):
            return None
        seen = listed(path, result, sized)
        if seen is not None:
            return seen
        if CORRUPT.search(result):
            return False
        if not redirect and any(line.strip().lower() in (base, name) for line in result.splitlines()):
            return True
        place = name if name.startswith("/") else base
        if any(MISSING.search(line) and names_file(line.lower(), place) for line in result.splitlines()):
            return False
    return None


def own_output(result: str, entries: list[tuple[int, str, set[int]]], keep: set[int]) -> str:
    """The result without the error lines that belong only to steps outside `keep`, a Traceback with its frames and
    its exception line, and the line that chains two Tracebacks: what the caller reads of a copy whose maker sends its
    errors to /dev/null, so the Traceback of `cp a b 2>/dev/null; python3 -c ...` does not speak for the cp."""
    lines = result.splitlines()
    drop: set[int] = set()
    for n, _, owners in entries:
        if not owners or owners & keep:
            continue
        drop.add(n)
        if TRACEBACK.match(lines[n]):
            end = n + 1
            while end < len(lines) and (not lines[end].strip() or lines[end][:1].isspace()):
                end += 1
            drop.update(range(n, min(end + 1, len(lines))))
    drop.update(n for n, line in enumerate(lines) if CHAINED.match(line) and n + 2 < len(lines)
                and any(m in drop for m in range(n + 1, min(n + 3, len(lines))) if TRACEBACK.match(lines[m])))
    return "\n".join(line for n, line in enumerate(lines) if n not in drop)


def copier_steps(path: str, all_steps: list[Step]) -> list[int]:
    """The steps that make the copy at `path`: those that write it or copy onto or from its name; else a LibreOffice
    --convert-to that names its stem; else the first program that can write a file by itself (an interpreter, git, tar,
    unzip) whose text or here-document names it. Empty where no step names it: a reader of the copy never made it."""
    base = PurePosixPath(path.rstrip(";,)")).name.lower()
    if not base:
        return []
    writes = [k for k, s in enumerate(all_steps)
              if base in {PurePosixPath(w).name.lower() for w in s.stage.writes}
              or (s.stage.program in COPY_PROGRAMS and base in s.names())]
    if writes:
        return writes
    stem = PurePosixPath(base).stem
    named = re.compile(r"(?<![\w.-])" + re.escape(stem) + r"\.\w+")
    converts = [k for k, s in enumerate(all_steps) if s.stage.program in CONVERTERS and named.search(s.text.lower())]
    if converts:
        return converts
    return next(([k] for k, s in enumerate(all_steps) if (s.interpreter or s.stage.program in MAKERS)
                 and base in (s.text + "\n" + s.body).lower()), [])


def blocked(k: int, owners: set[int], all_steps: list[Step], result: str) -> bool | None:
    """Whether step k failed by the result, where no step proves it succeeded (proven): it printed an error line, or a
    step it waits on did, or a step after its || ran (printed an error, or an echo of its words: `cp a b 2>/dev/null ||
    echo SHUT`). A step that sends its errors to /dev/null fails where the same program printed one elsewhere in the
    call (`git show a:f > x 2>/dev/null` beside a `git log` that found no repository); else its output cannot tell
    (None)."""
    step = all_steps[k]
    if owners & {k, *step.waits_on} or any(r in owners or echoed(all_steps[r], result) for r in step.rescued_by):
        return True
    if step.silent:
        return True if any(all_steps[o].stage.program == step.stage.program for o in owners) else None
    return False


def proven(k: int, all_steps: list[Step], done: set[int]) -> bool:
    """Whether a step that runs only once step k succeeded ran."""
    return any(k in all_steps[j].proves for j in done)


def made_by_redirect(path: str, all_steps: list[Step]) -> bool:
    """Whether a step writes the copy through a redirect, which leaves an empty file where the command fails."""
    base = PurePosixPath(path.rstrip(";,)")).name.lower()
    return any(base in {PurePosixPath(w).name.lower() for w in s.stage.writes} for s in all_steps)


def into_own_file(path: str, all_steps: list[Step]) -> bool:
    """Whether a step that writes the copy through a redirect sends its errors into it too."""
    base = PurePosixPath(path.rstrip(";,)")).name.lower()
    return any(s.into_file and base in {PurePosixPath(w).name.lower() for w in s.stage.writes} for s in all_steps)


def copy_verdicts(cmd: str, result: str, made: set[str]) -> dict[str, bool | None]:
    """Each copy of `made`: True where it landed, False where its making failed, None where the call's output cannot
    tell. It landed where a step that makes it is proven to have succeeded (a step after it through && ran). Else it
    failed where a later command of the call found it missing, or no step that makes it succeeded (blocked). Where the
    output cannot tell (a maker that sends its errors to /dev/null, a copy no step names, the tool's own error), a
    listing decides (listed), then a line that names it unless a redirect made it; else None. An empty result fails
    no copy whose makers speak."""
    all_steps = steps(cmd)
    if not result.strip():
        return {path: None if any(all_steps[k].silent for k in copier_steps(path, all_steps)) else True
                for path in made}
    entries = error_entries(result, all_steps)
    errors = None if entries is None else [(line, owners) for _, line, owners in entries]
    owners = set().union(*(own for _, own in errors)) if errors else set()
    done = ran(errors, all_steps, result) if errors is not None else set()
    out: dict[str, bool | None] = {}
    for path in made:
        copiers = copier_steps(path, all_steps)
        if errors is not None and any(proven(k, all_steps, done) for k in copiers):
            out[path] = True
            continue
        verdicts = {blocked(k, owners, all_steps, result) for k in copiers} if errors is not None else {None}
        if copiers and (missing_after(path, copiers, errors or []) or corrupt_after(path, copiers, result, all_steps)):
            out[path] = False
        elif False in verdicts:
            out[path] = True
        elif not copiers or None in verdicts:
            seen = listed(path, result, not into_own_file(path, all_steps)) if errors is not None else None
            if seen is None and errors is not None and not made_by_redirect(path, all_steps) \
                    and shown(path, result, errors):
                seen = True
            out[path] = seen
        else:
            out[path] = False
    return out


def failed_copies(cmd: str, result: str, made: set[str], whole_failed: Callable[[str], bool],
                  later: Iterable[tuple[str, str]] = ()) -> set[str]:
    """The copies of `made` whose making failed (copy_verdicts). A copy the call's output cannot tell of is decided by
    the agent's later calls (later_verdict), else by the caller's reading (whole_failed) of the output that the call's
    other steps do not own (own_output), or of the whole result where no step names the copy or the result is the
    tool's own error. An empty output fails nothing."""
    later = list(later)
    all_steps = steps(cmd)
    entries = error_entries(result, all_steps) if result.strip() else []
    failed = set()
    for path, verdict in copy_verdicts(cmd, result, made).items():
        if verdict is None:
            verdict = later_verdict(path, later, made_by_redirect(path, all_steps), not into_own_file(path, all_steps))
        if verdict is None:
            copiers = copier_steps(path, all_steps)
            keep = {j for k in copiers for j in (k, *all_steps[k].waits_on, *all_steps[k].rescued_by)}
            read = own_output(result, entries, keep) if copiers and entries is not None else result
            verdict = not (read.strip() and whole_failed(read))
        if not verdict:
            failed.add(path)
    return failed
