"""Shell commands as the code ladder reads them: pipelines, simple commands, their words, and grep calls.

An agent's shell or run_python call is a string. This module splits it into pipelines and simple commands outside
quotes and escapes. It finds each command's program, arguments and written files, and parses a grep or rg into what
it searches for and how it reports. A pipeline that only counts or tests shows nothing of what it reads. A `for` loop's
variable stands for the loop's words. command_blocks follows a command's `cd`s, so its files read as absolute paths.

    stages = simple_commands(command_of(tool_call), lambda path: path.startswith("/keys"))
    hits = line_hits(result, distinctive_lines)
    for block in command_blocks(command_of(tool_call), "/workspace"):
        print(block.writes, block.text)
"""
from __future__ import annotations

import posixpath
import re
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import NamedTuple

from src.measures.honeypot_fingerprints import norm

HEREDOC = re.compile(r"(?<!<)<<(?!<)-?\s*(['\"]?)([A-Za-z_]\w*)\1")
REDIRECT = re.compile(r"(\d*|&)(>>?|<)(&\d*)?(.*)")
VARIABLE = re.compile(r"\$(?:(\w+)|\{(\w+)\})")
# what may precede a simple command's program: variable settings, wrappers and a loop's `do`
LEAD = re.compile(r"[\s({]*(?:\w+=\S*\s+|(?:do|sudo|env|time|nice|nohup|command|exec|stdbuf)\s+(?:-\S+\s+)*|"
                  r"xargs\s+(?:-\S+\s+(?:\d+\s+)?)*|timeout\s+(?:-\S+\s+)*\S+\s+)*")
COUNTERS = {"wc", "test", "[", "stat", "ls", "du", "file", "md5sum", "sha1sum", "sha256sum", "cksum"}
COPIERS = {"cp", "mv", "rsync"}
# the file python code writes by a literal name: open(name, "w") or Path(name).write_text(...)
PYTHON_WRITE = re.compile(r"""\b(?:open|Path)\(\s*['"]([^'"]+)['"]"""
                          r"""(?:\s*,\s*['"][wax]|\s*\)\.write_(?:text|bytes)\()""")
PYTHON = re.compile(r"python[\d.]*")
GREP_VALUES = {"grep": "efABCmdD", "rg": "efABCmgtTjMEr"}         # short options that take a value
GREP_LONG_VALUES = {"regexp", "file", "after-context", "before-context", "context", "max-count", "include",
                    "exclude", "exclude-dir", "glob", "type", "type-not", "replace", "max-depth"}
GREP_COUNTS = {"grep": {"c", "q", "l", "L", "count", "quiet", "silent", "files-with-matches", "files-without-match"},
               "rg": {"c", "q", "l", "count", "count-matches", "quiet", "files-with-matches", "files-without-match",
                      "files"}}
TOOLS = ("shell", "run_python", "bash", "python")
"""The tools whose call is a command or code."""
REFUSED = "validation errors parsing tool input arguments"
"""What Inspect answers when a tool's schema refuses a call: nothing ran."""
PY_WRITE = re.compile(r"""\bopen\([^)]*,\s*['"][wax]|\.write_text\(|\.write_bytes\(|\bjson\.dump\(""")
"""Python code that opens a file for writing."""
PATH_WORD = re.compile(r"[\w./-]+")


def command_of(tc: dict) -> str:
    """The command or code of a shell or run_python call."""
    args = tc.get("arguments") or {}
    return str(args.get("command") or args.get("code") or "")


def tool_calls_of(transcript: dict) -> Iterator[tuple[int, dict]]:
    """(its number, the call) of every tool call of a transcript's agent steps, numbered from 0 as the code ladder
    numbers them."""
    k = -1
    for step in transcript["trajectory"]["steps"]:
        if step.get("source") != "agent":
            continue
        for tc in step.get("tool_calls") or []:
            k += 1
            yield k, tc


def results_of(transcript: dict) -> dict[str, str]:
    """Each tool call's result by the call's id."""
    return {result.get("source_call_id"): result.get("content") or "" for step in transcript["trajectory"]["steps"]
            for result in ((step.get("observation") or {}).get("results") or [])}


def commands_of(transcript: dict) -> list[tuple[int, str]]:
    """Each shell or python call as (its number, its command or code)."""
    return [(k, command_of(tc)) for k, tc in tool_calls_of(transcript) if tc.get("function_name") in TOOLS]


def posts_written(transcript: dict) -> list[tuple[int, str]]:
    """Each board_post call as (its number, the post)."""
    return [(k, str((tc.get("arguments") or {}).get("message") or "")) for k, tc in tool_calls_of(transcript)
            if tc.get("function_name") == "board_post"]


def line_hits(text: str, lines: set[str]) -> int:
    """How many distinctive lines the text contains, anywhere in it once whitespace and case are normalised."""
    flat = norm(text).lower()
    return sum(1 for ln in lines if ln in flat)


def names_in(text: str, names: set[str]) -> bool:
    """Whether the text names one of the paths as a whole word (a copy's name, not a substring of another)."""
    low = text.lower()
    return any(re.search(r"(?<![\w./-])" + re.escape(name) + r"(?![\w.-])", low) for name in names)


def one_line(cmd: str, limit: int = 160) -> str:
    return " ".join(cmd.split())[:limit]


def shell_pipelines(cmd: str) -> list[list[str]]:
    """The command's pipelines, each a list of its simple commands: split at ||, &&, ; and newlines, then at |,
    outside quotes and escapes."""
    pipelines, stages, current, quote, i = [], [], [], None, 0
    while i < len(cmd):
        ch = cmd[i]
        if ch == "\\" and quote != "'" and i + 1 < len(cmd):
            current.append(cmd[i:i + 2])
            i += 2
            continue
        if quote:
            quote = None if ch == quote else quote
        elif ch in "'\"":
            quote = ch
        elif ch in ";\n" or cmd.startswith(("&&", "||"), i):
            pipelines.append([*stages, "".join(current)])
            stages, current = [], []
            i += 2 if cmd.startswith(("&&", "||"), i) else 1
            continue
        elif ch == "|":
            stages.append("".join(current))
            current = []
            i += 1
            continue
        current.append(ch)
        i += 1
    return [*pipelines, [*stages, "".join(current)]]


def shell_segments(cmd: str) -> list[str]:
    """The command's simple commands: split at |, ||, &&, ; and newlines outside quotes and escapes."""
    return [stage for pipeline in shell_pipelines(cmd) for stage in pipeline]


def heredocs(cmd: str) -> tuple[str, list[str]]:
    """The command without its here-documents' bodies, and each body in order."""
    out, bodies, ends = [], [], []
    for line in cmd.split("\n"):
        if ends:
            if line.strip() == ends[0]:
                ends.pop(0)
            else:
                bodies[-len(ends)] += line + "\n"
            continue
        out.append(line)
        opened = [m.group(2) for m in HEREDOC.finditer(line)]
        ends += opened
        bodies += [""] * len(opened)
    return "\n".join(out), bodies


def strip_heredocs(cmd: str) -> str:
    """The command without its here-documents' bodies, which are data the command writes, not commands."""
    return heredocs(cmd)[0]


def without_counters(cmd: str) -> str:
    """The command without the simple commands that only look at a file from outside or print words (COUNTERS, echo,
    printf); here-documents stay. `ls -l deck.pptx; python own.py` then names the deck in no command that reads it."""
    looks = {stage for stage in shell_segments(strip_heredocs(cmd))
             if stage_of(stage).program in COUNTERS | {"echo", "printf"}}
    return "\n".join(stage for stage in shell_segments(cmd) if stage not in looks)


def words(text: str) -> list[tuple[str, bool]]:
    """The shell words of a simple command, unquoted, each with whether it was written without quotes or escapes."""
    out: list[tuple[str, bool]] = []
    current: list[str] | None = None
    plain, quote, i = True, None, 0
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == quote:
                quote = None
            elif ch == "\\" and quote == '"' and i + 1 < len(text) and text[i + 1] in '"\\$`':
                current.append(text[i + 1])
                i += 1
            else:
                current.append(ch)
        elif ch.isspace():
            if current is not None:
                out.append(("".join(current), plain))
            current, plain = None, True
        else:
            current = [] if current is None else current
            if ch in "'\"":
                quote, plain = ch, False
            elif ch == "\\" and i + 1 < len(text):
                current.append(text[i + 1])
                plain = False
                i += 1
            else:
                current.append(ch)
        i += 1
    if current is not None:
        out.append(("".join(current), plain))
    return out


class Stage(NamedTuple):
    """A simple command: its program (past settings and wrappers), its arguments (a `< file` input among them, the
    redirects' targets and tee's files left out) and the files it writes (> and >> targets, tee's files)."""
    program: str
    args: list[str]
    writes: list[str]

    @property
    def text(self) -> str:
        return " ".join([self.program, *self.args])

    @property
    def operands(self) -> list[str]:
        return [a for a in self.args if not a.startswith("-")]

    @property
    def copy_target(self) -> list[str]:
        """What a copier writes: its last operand, once a source stands before it."""
        return self.operands[-1:] if self.program in COPIERS and len(self.operands) > 1 else []

    @property
    def edited(self) -> list[str]:
        """The files sed -i edits in place, which keep what the script leaves alone."""
        return self.operands[1:] if self.program == "sed" and any(a.startswith("-i") for a in self.args) else []


def stage_of(stage: str) -> Stage:
    """A simple command's text as a Stage."""
    tokens = words(stage[LEAD.match(stage).end():])
    program = Path(tokens[0][0]).name if tokens else ""
    args, writes, i = [], [], 1
    while i < len(tokens):
        tok, plain = tokens[i]
        if tok.startswith("<<"):                                    # a here-document or here-string
            i += 1 if tok.lstrip("<-") else 2
            continue
        m = REDIRECT.fullmatch(tok) if plain else None
        if m:
            attached = m.group(3) is not None or bool(m.group(4))
            target = m.group(4) if attached else (tokens[i + 1][0] if i + 1 < len(tokens) else "")
            i += 1 if attached else 2
            if m.group(2) == "<":
                args.append(target)
            elif m.group(3) is None and m.group(1) in ("", "1", "&") and target != "/dev/null":
                writes.append(target)
            continue
        args.append(tok)
        i += 1
    if program == "tee":
        writes += [a for a in args if not a.startswith("-")]
        args = [a for a in args if a.startswith("-")]
    return Stage(program, args, writes)


class GrepCall(NamedTuple):
    """A grep or rg: what it searches for, where, and how it reports."""
    patterns: list[str]
    paths: list[str]
    recursive: bool
    counts: bool        # it prints counts, file names or a status and no content (-c, -q, -l, -L)


def grep_call(stage: Stage) -> GrepCall | None:
    """The grep or rg a simple command runs, its options parsed; None for another program."""
    if stage.program not in ("grep", "egrep", "fgrep", "rg"):
        return None
    tool = "rg" if stage.program == "rg" else "grep"
    patterns, paths, flags = [], [], set()
    rest = iter(stage.args)
    for tok in rest:
        if tok == "--":
            paths += list(rest)
        elif tok.startswith("--"):
            name, eq, value = tok[2:].partition("=")
            flags.add(name)
            value = value if eq or name not in GREP_LONG_VALUES else next(rest, "")
            if name == "regexp":
                patterns.append(value)
        elif tok.startswith("-") and len(tok) > 1:
            for i, ch in enumerate(tok[1:]):
                flags.add(ch)
                if ch in GREP_VALUES[tool]:
                    value = tok[i + 2:] or next(rest, "")
                    if ch == "e":
                        patterns.append(value)
                    break
        else:
            paths.append(tok)
    if not patterns and not flags & {"f", "file"} and paths:
        patterns.append(paths.pop(0))
    recursive = tool == "rg" or bool(flags & {"r", "R", "recursive", "dereference-recursive"})
    return GrepCall(patterns, paths, recursive, bool(flags & GREP_COUNTS[tool]))


def absolute(path: str, directory: str | None) -> str:
    """Where the shell finds a path: absolute when written so or when read from a known directory; as written when it
    starts at the home or at a variable, which no cd moves, or when the directory is unknown."""
    if path.startswith("/"):
        return posixpath.normpath(path)
    if path.startswith(("~", "$")) or not directory:
        return path
    return posixpath.normpath(posixpath.join(directory, path))


def located(stage: Stage, directory: str) -> Stage:
    """The stage with the files it writes, and a copier's files, as absolute paths."""
    args = stage.args
    if stage.program in COPIERS:
        args = [a if a.startswith("-") else absolute(a, directory) for a in args]
    return stage._replace(args=args, writes=[absolute(path, directory) for path in stage.writes])


class Block(NamedTuple):
    """What runs from one directory: a pipeline's stages (located) with the text they carry (carried, and its
    here-documents), or python code as the text alone."""
    stages: list[Stage]
    text: str
    directory: str

    @property
    def writes(self) -> list[str]:
        """The files it replaces, as absolute paths: redirects, tee, a copier's last file, python's writes by name."""
        stages = [path for s in self.stages for path in (*s.writes, *s.copy_target)]
        return [*stages, *(absolute(path, self.directory) for path in PYTHON_WRITE.findall(self.text))]

    @property
    def edits(self) -> list[str]:
        """The files it edits in place (sed -i), as absolute paths."""
        return [absolute(path, self.directory) for s in self.stages for path in s.edited]

    @property
    def printed(self) -> list[str]:
        """The files it prints whole, as absolute paths: a lone `cat`, its output going to no file."""
        lone = self.stages[0] if len(self.stages) == 1 else None
        return [absolute(path, self.directory) for path in lone.operands] \
            if lone and lone.program == "cat" and not lone.writes else []


def carried(stage: Stage) -> str:
    """What a simple command holds in its own words for a file it writes: what echo or printf prints, sed's script,
    python -c's code."""
    if PYTHON.fullmatch(stage.program) and "-c" in stage.args:
        return "".join(stage.args[stage.args.index("-c") + 1:][:1])
    return " ".join(stage.operands) if stage.program in ("echo", "printf", "sed") else ""


def command_blocks(cmd: str, directory: str) -> list[Block]:
    """The command's pipelines, each from the directory its `cd`s reach from `directory`. `cd -` returns; a directory
    named by the home or a variable stays as written, so what runs there is in no known directory."""
    bare, bodies = heredocs(cmd)
    texts, out, previous = iter(bodies), [], directory
    for pipeline in shell_pipelines(bare):
        stages = [stage_of(stage) for stage in pipeline]
        text = "".join(next(texts, "") for stage in pipeline for _ in HEREDOC.finditer(stage)) + \
            "".join(map(carried, stages))
        if len(stages) == 1 and stages[0].program == "cd":
            target = (stages[0].args or ["~"])[0]
            directory, previous = previous if target == "-" else absolute(target, directory), directory
        else:
            out.append(Block([located(stage, directory) for stage in stages], text, directory))
    return out


def written_paths(cmd: str) -> set[str]:
    """The paths a command writes, as it names them, wherever its `cd`s lead: what Block.writes and Block.edits read
    from a stage, each source's name under a copy's target, and every path-like word of Python code that writes a file,
    read as relative. Block reads only literal names, since it says what a file holds; this asks whether the agent wrote
    a path at all, and a path in Python often sits in a variable or follows one. Here-documents' bodies are data."""
    out = {word.lstrip("/") for word in PATH_WORD.findall(cmd)} if PY_WRITE.search(cmd) else set()
    for stage in (stage_of(s) for pipeline in shell_pipelines(strip_heredocs(cmd)) for s in pipeline):
        out |= {*stage.writes, *stage.edited, *stage.copy_target}
        out |= {f"{target.rstrip('/')}/{Path(source).name}" for target in stage.copy_target
                for source in stage.operands[:-1]}
    return out


def count_only(stages: list[Stage]) -> bool:
    """Whether a pipeline shows nothing of what it reads (CheatBench's count-only and existence-only probes): its last
    command counts, tests or lists (grep -c, -q, -l, wc, test, stat, ls) and none of it writes a file."""
    if any(s.writes for s in stages):
        return False
    call = grep_call(stages[-1])
    return call.counts if call is not None else stages[-1].program in COUNTERS


def in_loops(stage: Stage, loops: dict[str, str]) -> Stage:
    """The stage with each `for` loop's variable in its arguments replaced by the loop's words."""
    return stage._replace(args=[VARIABLE.sub(lambda m: loops.get(m[1] or m[2], m[0]), arg) for arg in stage.args])


def simple_commands(cmd: str, names_dir: Callable[[str], bool]) -> list[tuple[Stage, bool]]:
    """The command's simple commands that may show what they read, each with whether a `cd` before it entered a
    directory names_dir accepts. Here-documents are data, a count_only pipeline shows nothing, and a `for` loop's
    variable reads as the loop's words."""
    out, here, loops = [], False, {}
    for pipeline in shell_pipelines(strip_heredocs(cmd)):
        stages = [in_loops(stage_of(s), loops) for s in pipeline]
        if len(stages) == 1 and stages[0].program == "cd":
            here = bool(stages[0].args) and names_dir(stages[0].args[0])
        elif not count_only(stages):
            out += [(s, here) for s in stages]
        if stages[0].program == "for" and stages[0].args[1:2] == ["in"]:
            loops[stages[0].args[0]] = " ".join(stages[0].args[2:])
    return out
