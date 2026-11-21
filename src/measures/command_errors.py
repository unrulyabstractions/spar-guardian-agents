"""Which simple command of a shell call an error line of the call's combined output belongs to, and so which of the
copies the call made failed (Domain.copies_made). A call's result holds every command's output: `cp deck.pptx
to_send/ && python fix.py` prints python's Traceback first although the copy landed. Each step of a chain is judged by
its own evidence. A copy landed when a command that runs only once its maker succeeded ran: its error line, its echo,
or output that only such commands could print (`git show a:f > x && python -c "print(zipfile.ZipFile(x).namelist())"`
and the archive's names). A copy failed when its own command printed an error, or a command it waited on through && or
a pipe did (`cd missing && cp ...` never ran the cp), or a fallback that runs only once it failed ran (`cp a b
2>/dev/null || ls /x` and the listing of /x), or a later command found it missing, or a long listing of its directory
after it does not list it. A call the tool refused never ran, so it made no copy; a call that timed out ran in part, so
only evidence lands its copies. Where the output cannot tell, the agent's later calls decide, then the caller's reading
of the result, with the lines other commands own taken out; where no command of the call makes the copy by its name,
of the whole result."""
from __future__ import annotations

import posixpath
import re
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from src.measures.shell_commands import (
    CHANGERS,
    GIT_OPTION_VALUES,
    HEREDOC,
    INTERPRETER,
    REDIRECT,
    SHELL,
    Stage,
    heredocs,
    stage_of,
    words,
)

COPY_PROGRAMS = {"cp", "mv", "rsync", "ln", "install"}
CONVERTERS = {"soffice", "libreoffice"}
MAKERS = {"git", "tar", "unzip", "zip", "dd"}
"""Programs beside the interpreters that write a file they name without a redirect."""
GIT_WORKTREE = {"checkout", "switch", "restore", "reset", "merge", "pull", "stash", "cherry-pick", "worktree", "clone",
                "am", "apply", "rebase", "revert"}
"""The git verbs that write the files of a working tree: one makes a copy its command need not name (`git checkout
agent-014` and the Riemann.zip of that branch)."""
SHELL_ERROR = re.compile(r"(?:\S*/)?(?:bash|sh|dash|zsh): (?:line \d+: )?([^:]+): (.*)")
PROGRAM_ERROR = re.compile(r"([\w.+-]+): (.*)")
GIT_ERROR = re.compile(r"(?:fatal|error): ")
CONVERTER_ERROR = re.compile(r"Error: ")
TRACEBACK = re.compile(r"Traceback \(most recent call last\):")
TRACE_FILE = re.compile(r'\s*File "([^"]+)", line \d+')
CHAINED = re.compile(r"(?:During handling of the above exception|The above exception was the direct cause)")
TIMED_OUT = re.compile(r"\s*Command timed out")
"""The tool's error for a command that ran past its time limit: it ran in part. Any other tool error is a refusal: the
command never ran (`Found 1 validation errors parsing tool input arguments`)."""
TOOL_NOTE = re.compile(r"The output of your call to \w+ was too long|Here is a truncated version:|"
                       r"<(?:START|END)_TOOL_OUTPUT>|Command timed out before completing")
"""The tool's own lines: around an output it cut, or for a command it stopped."""
MISSING = re.compile(r"No such file or directory|cannot stat|cannot access|does not exist", re.I)
LISTING = re.compile(r"-[rwxsStT-]{9}[.+@]?\s+\d+\s+\S+\s+\S+\s+(\d+(?:[.,]\d+)?[KMGTP]?)\s+\w{3}\s+\d+\s+"
                     r"(?:\d+:\d+|\d{4})\s+(.+?)(?: -> .*)?$")
"""A regular file's line of `ls -l`: its size and its name."""
ENTRY = re.compile(r"[-dlcbpsD][rwxsStT-]{9}[.+@]?\s+\d+\s+\S+\s+\S+\s+(?:\d+,\s*)?\d+(?:[.,]\d+)?[KMGTP]?\s+\w{3}\s+"
                   r"\d+\s+(?:\d+:\d+|\d{4})\s+(.+?)(?: -> .*)?$")
"""Any entry's line of `ls -l` (a file, a directory, a link): its name."""
TOTAL = re.compile(r"total \d+(?:[.,]\d+)?[KMGTP]?")
"""The line that opens a long listing of a directory."""
LISTING_HEADER = re.compile(r"(/[^\s:]*):")
SILENT = re.compile(r"(?:(?<![\w>&])2|&)>>?\s*(?!&)[^\s;&|]+|(?<![\d&>])>>?\s*(?!&)[^\s;&|]+\s+2>&1")
"""A command whose errors go to /dev/null or a file (`2>/dev/null`, `2>err.txt`, `&>log`, `> out 2>&1`): it prints no
error line."""
INTO_FILE = re.compile(r"(?<![\d&>])>>?\s*(?!&)(?!/dev/null\b)[^\s;&|]+\s+2>&1|&>>?\s*(?!/dev/null\b)[^\s;&|]+")
"""A command whose errors go into the file it writes (`git show a:f > f 2>&1`), so the file's size proves nothing."""
CORRUPT = re.compile(r"BadZipFile|not a zip file|End-of-central-directory signature not found|PackageNotFoundError|"
                     r"Package not found at")
"""A reader's error that says a file is no archive, a pptx or docx: the copy it read holds no content."""
COMPLAINT = re.compile(r"\b(?:error|errno|fatal|usage|warn(?:ing)?|hint|cannot|can't|couldn't|could not|unable|denied|"
                       r"not found|no such|invalid|fail(?:ed|ure|s)?|abort(?:ing|ed)?|unrecognized|unknown|illegal|"
                       r"missing|refus\w*|exception|traceback|permission|not a)\b", re.I)
"""A line that reads as a complaint, which any command may print on its error stream: it tells no step's output."""
QUIET = {"cd", "mkdir", "rm", "rmdir", "touch", "chmod", "chown", "ln", "cp", "mv", "install", "rsync", "export",
         "unset", "true", "false", ":", "sleep", "test", "[", "[[", "umask", "truncate", "unlink", "wait", "exit",
         "set", "shopt", "trap", "alias"}
"""Programs that print nothing as they succeed, unless asked to be verbose (-v)."""
QUIET_ERRORS = {"cat", "ls", "head", "tail", "wc", "grep", "egrep", "fgrep", "find", "sed", "awk", "sort", "uniq",
                "cut", "tr", "sha256sum", "sha1sum", "md5sum", "cksum", "stat", "file", "du", "df", "echo", "printf",
                "pwd", "diff", "cmp", "base64", "xxd", "od", "strings", "tee", "unzip", "zip", "realpath", "readlink",
                "basename", "dirname", "date", "which", "whoami", "id", "jq", "nl", "tac", "paste", "column", "hexdump",
                "touch", "mkdir"}
"""Programs whose error stream carries only complaints (COMPLAINT) or lines that name them (`ls: cannot access`)."""
GIT_QUIET_ERRORS = {"show", "cat-file", "archive", "diff", "log", "rev-parse", "ls-tree", "ls-files", "grep", "blame",
                    "config", "rev-list", "show-ref", "for-each-ref", "branch", "tag", "status", "describe", "shortlog"}
"""The git verbs whose error stream carries only `fatal:`, `error:`, `warning:` and `hint:` lines."""
KEYWORDS = {"if", "then", "else", "elif", "while", "until", "!"}
"""Words of the shell's grammar that may stand before a command (stage_of strips only `do`)."""
SILENT_KEYWORDS = {"fi", "done", "esac", "for", "case", "select", "in", "}", "{"}
"""Words of the shell's grammar that stand alone as a step and print nothing."""
INFALLIBLE = {"echo", "printf", "true", ":"}
"""Commands that do not fail once they run (with no redirect to a file)."""


@dataclass
class Step:
    """One simple command of a call: its text, its here-documents' bodies, its stage, the commands before it that it
    waits on (&& and |) in the order they run, the commands that run only once it did not succeed (`a || b`: b ran,
    so a failed; `a && echo ok || b`: b ran, so a failed, since echo cannot), the commands whose success its running
    proves (`a && b`: b ran, so a exited 0; `a | b` proves nothing of a; `(a || b) && c` proves neither), whether its
    output goes into a pipe, and the groups `( )` or `{ }` it stands in, outermost first."""
    text: str
    body: str
    stage: Stage
    waits_on: list[int] = field(default_factory=list)
    rescued_by: list[int] = field(default_factory=list)
    proves: list[int] = field(default_factory=list)
    piped: bool = False
    scope: tuple[int, ...] = ()

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

    @property
    def command(self) -> Stage:
        """Its stage past a leading `if`, `then`, `else`, `elif`, `while`, `until` or `!`."""
        text = self.text
        while (parts := text.split(None, 1)) and parts[0] in KEYWORDS:
            text = parts[1] if len(parts) > 1 else ""
        return self.stage if text == self.text else stage_of(text)

    @property
    def redirected(self) -> bool:
        """Whether its standard output goes to a file or /dev/null (`>`, `>>`, `1>`, `&>`; `>&2` keeps it shown)."""
        if self.command.program == "tee":
            return False
        for token, plain in words(self.text):
            m = REDIRECT.fullmatch(token) if plain else None
            if m and m.group(2) in (">", ">>") and m.group(1) in ("", "1", "&") and m.group(3) is None:
                return True
        return False

    @property
    def speaks(self) -> bool:
        """Whether it may print a line into the result that is no complaint: its standard output reaches the result
        and it is no QUIET program (tar without t or v, unzip -q, cd without -), or its error stream reaches the result
        and may carry more than complaints (an interpreter, curl, a git checkout: anything not in QUIET_ERRORS)."""
        stage = self.command
        program, args = stage.program, stage.args
        if program in SILENT_KEYWORDS:
            return False
        flags = "".join(a[1:] for a in args if a.startswith("-") and not a.startswith("--"))
        verbose = "v" in flags or "--verbose" in args
        if program in QUIET:
            quiet_out = not verbose and not (program == "cd" and "-" in args) and not (program == "set" and not args)
        elif program == "tar":
            mode = flags + (args[0] if args and not args[0].startswith("-") else "")
            quiet_out = not ({"t", "v"} & set(mode)) and not {"--list", "--verbose"} & set(args)
        elif program == "unzip":
            quiet_out = "q" in flags and not set("lvtZpcz") & set(flags)
        else:
            quiet_out = False
        out = not (self.piped or self.redirected or quiet_out)
        if program == "git":
            quiet_err = git_verb(stage) in GIT_QUIET_ERRORS
        else:
            quiet_err = program in QUIET or program in QUIET_ERRORS
        err = not self.silent and not (self.piped and "2>&1" in self.text) and not quiet_err
        return out or err

    @property
    def said(self) -> str | None:
        """The one line an echo prints whose words are literal: no variable, substitution, escape or glob, no -n."""
        if self.stage.program != "echo" or self.redirected or self.piped:
            return None
        if any(a.startswith("-") and "n" in a for a in self.stage.args):
            return None
        said = " ".join(a for a in self.stage.args if not a.startswith("-")).strip()
        return said if said and not any(c in said for c in "$`\\*?[") else None

    def names(self) -> set[str]:
        """The base names, lower-cased, of the files it names: operands and the files it writes."""
        return {PurePosixPath(a).name.lower() for a in [*self.stage.operands, *self.stage.writes] if a}


def git_verb(stage: Stage) -> str:
    """The verb of a git command: its first word that is no option or an option's value (`git -C /x show`)."""
    args = iter(stage.args)
    for arg in args:
        if arg in GIT_OPTION_VALUES:
            next(args, None)
        elif not arg.startswith("-"):
            return arg
    return ""


def tokens(text: str) -> list[tuple[str, str]]:
    """The call's simple commands and operators in order: ("cmd", its text) and ("op", one of && || | ; ( )), a
    newline as ;. A ( or { that opens a command opens a group; a ( inside a word (`$(`, `<(`) stays in the word, as
    its ) does, and the operators inside it still split. A ) that closes nothing ends no group."""
    out: list[tuple[str, str]] = []
    current: list[str] = []
    stack: list[str] = []
    quote, i = None, 0

    def flush() -> None:
        cmd = "".join(current).strip()
        current.clear()
        while cmd.endswith(")") and cmd.count(")") > cmd.count("("):
            cmd = cmd[:-1].rstrip()
        if cmd:
            out.append(("cmd", cmd))

    while i < len(text):
        ch = text[i]
        if ch == "\\" and quote != "'" and i + 1 < len(text):
            current.append(text[i:i + 2])
            i += 2
            continue
        blank = not "".join(current).strip()
        op = None
        if quote:
            quote = None if ch == quote else quote
        elif ch in "'\"":
            quote = ch
        elif ch == "(" and blank:
            stack.append("(")
            op = "("
        elif ch == "{" and blank and text[i + 1:i + 2].isspace():
            stack.append("{")
            op = "("
        elif ch == "(":
            stack.append("$")
        elif ch == ")" and stack and stack[-1] == "(":
            stack.pop()
            op = ")"
        elif ch == ")" and stack and stack[-1] == "$":
            stack.pop()
        elif ch == "}" and blank and stack and stack[-1] == "{":
            stack.pop()
            op = ")"
        elif text.startswith("&&", i):
            op = "&&"
        elif text.startswith("||", i):
            op = "||"
        elif ch in ";\n":
            op = ";"
        elif ch == "|":
            op = "|"
        if op is None:
            current.append(ch)
            i += 1
            continue
        flush()
        out.append(("op", op))
        i += 2 if op in ("&&", "||") else 1
    flush()
    return out


@dataclass
class _Node:
    kind: str                                   # cmd, and, or, pipe, seq, group
    parts: list[_Node] = field(default_factory=list)
    index: int = -1


def _parse(toks: list[tuple[str, str]]) -> tuple[list[_Node], list[str]]:
    """The call's top-level statements as trees (a list of statements, and-or lists, pipelines, groups and simple
    commands) and the simple commands' texts in order."""
    texts: list[str] = []
    pos = 0

    def peek() -> tuple[str, str] | None:
        return toks[pos] if pos < len(toks) else None

    def newlines() -> None:
        nonlocal pos
        while peek() == ("op", ";"):
            pos += 1

    def unit() -> _Node | None:
        nonlocal pos
        tok = peek()
        if tok is not None and tok[0] == "cmd":
            pos += 1
            texts.append(tok[1])
            return _Node("cmd", index=len(texts) - 1)
        if tok == ("op", "("):
            pos += 1
            inner = statements()
            if peek() == ("op", ")"):
                pos += 1
            return _Node("group", [inner]) if inner is not None else None
        return None

    def pipeline() -> _Node | None:
        nonlocal pos
        left = unit()
        while left is not None and peek() == ("op", "|"):
            pos += 1
            newlines()
            right = unit()
            if right is None:
                break
            left = _Node("pipe", [left, right])
        return left

    def and_or() -> _Node | None:
        nonlocal pos
        left = pipeline()
        while left is not None and peek() in (("op", "&&"), ("op", "||")):
            op = toks[pos][1]
            pos += 1
            newlines()
            right = pipeline()
            if right is None:
                break
            left = _Node("and" if op == "&&" else "or", [left, right])
        return left

    def statements() -> _Node | None:
        nonlocal pos
        items: list[_Node] = []
        while pos < len(toks) and peek() != ("op", ")"):
            if peek() == ("op", ";"):
                pos += 1
                continue
            node = and_or()
            if node is None:
                pos += 1                        # an operator that stands where a command should
                continue
            items.append(node)
        return None if not items else items[0] if len(items) == 1 else _Node("seq", items)

    top: list[_Node] = []
    while pos < len(toks):
        node = statements()
        if node is not None:
            top.append(node)
        if peek() == ("op", ")"):
            pos += 1
    return top, texts


@dataclass
class _Info:
    steps: set[int]
    succeeded: set[int]         # the node succeeded: these steps succeeded
    failed: set[int]            # the node failed: these steps did not succeed
    waited: set[int]            # an error of these stops what follows the node through &&
    infallible: bool


def steps(cmd: str) -> list[Step]:
    """The call's simple commands in order, each with the commands it waits on, those that run only once it did not
    succeed and those its running proves succeeded, read from the shell's grammar: && and || bind left to right, a pipe
    binds tighter, a pipeline exits as its last command, a group as its last statement. Here-document bodies go to the
    command that opened them."""
    stripped, bodies = heredocs(cmd)
    top, texts = _parse(tokens(stripped))
    out: list[Step] = []
    for text in texts:
        opened = len(HEREDOC.findall(text))
        body = "".join(bodies.pop(0) for _ in range(min(opened, len(bodies))))
        out.append(Step(text, body, stage_of(text)))
    groups = [0]

    def walk(node: _Node, wait: set[int], prove: set[int], scope: tuple[int, ...]) -> _Info:
        if node.kind == "cmd":
            k = node.index
            step = out[k]
            step.waits_on, step.proves, step.scope = sorted(wait), sorted(prove), scope
            infallible = step.stage.program in INFALLIBLE and not step.stage.writes
            return _Info({k}, {k}, {k}, {k}, infallible)
        if node.kind == "group":
            groups[0] += 1
            return walk(node.parts[0], wait, prove, (*scope, groups[0]))
        if node.kind == "seq":
            infos = [walk(part, wait, prove, scope) for part in node.parts]
            last = infos[-1]
            return _Info(set().union(*(i.steps for i in infos)), last.succeeded, last.failed, last.waited,
                         last.infallible)
        left_node, right_node = node.parts
        if node.kind == "and":
            a = walk(left_node, wait, prove, scope)
            b = walk(right_node, wait | a.waited, prove | a.succeeded, scope)
            return _Info(a.steps | b.steps, a.succeeded | b.succeeded, b.failed | (a.failed if b.infallible else set()),
                         a.waited | b.waited, a.infallible and b.infallible)
        if node.kind == "or":
            a = walk(left_node, wait, prove, scope)
            b = walk(right_node, wait, prove, scope)
            for k in a.failed:
                out[k].rescued_by = sorted(set(out[k].rescued_by) | b.steps)
            return _Info(a.steps | b.steps, set(), a.failed | b.failed, b.waited, a.infallible or b.infallible)
        a = walk(left_node, wait, prove, scope)                 # a pipe
        for k in a.steps:
            out[k].piped = True
        b = walk(right_node, wait | a.waited, prove, scope)
        return _Info(a.steps | b.steps, b.succeeded, b.failed, a.waited | b.waited, b.infallible)

    for node in top:
        walk(node, set(), set(), ())
    return out


def names_file(line: str, name: str) -> bool:
    """Whether the line names the file by its whole base name (repo is not named in repository)."""
    return bool(name) and re.search(r"(?<![\w.-])" + re.escape(name) + r"(?![\w-])", line) is not None


def names_dir(line: str, path: str) -> bool:
    """Whether the line names the directory by its whole path, alone or with a path under it (/tmp/ref is not named in
    /tmp/ref.txt)."""
    return bool(path) and re.search(r"(?<![\w.-])" + re.escape(path) + r"(?![\w.-])", line) is not None


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


def error_entries(result: str, all_steps: list[Step]) -> list[tuple[int, str, set[int]]]:
    """error_lines with each line's index in the result."""
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


def error_lines(result: str, all_steps: list[Step]) -> list[tuple[str, set[int]]]:
    """The result's error lines, each with the steps it belongs to. A line belongs to a step by its prefix: the shell's
    `bash: line N: prog:` or `bash: line N: file:` for a redirect, a program's own `prog:`, git's `fatal:` and
    `error:`, LibreOffice's `Error:`, an interpreter's Traceback (by the file its frames name). A command that sends its
    errors to /dev/null owns none; a warning is no error. A line no step owns is kept, with no owner, where it says a
    file is missing."""
    return [(line, owners) for _, line, owners in error_entries(result, all_steps)]


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
    words_said = Counter(step.text for step in all_steps if step.stage.program in ("echo", "printf"))
    return out | {k for k, step in enumerate(all_steps) if words_said.get(step.text) == 1 and echoed(step, result)}


def long_listing(stage: Stage) -> bool:
    """Whether the stage is an `ls -l` (any bundle with l)."""
    return stage.program == "ls" and any(a.startswith("-") and not a.startswith("--") and "l" in a for a in stage.args)


def listing_flags(stage: Stage) -> str:
    return "".join(a[1:] for a in stage.args if a.startswith("-") and not a.startswith("--"))


def most_listings(step: Step) -> int | None:
    """How many long listings of a directory (TOTAL lines) the step can print: none for one whose output goes to a
    file, one per operand (one with none), None where it may print any number (-R, a glob, a variable)."""
    stage = step.command
    if step.redirected:
        return 0
    if "R" in listing_flags(stage) or any(c in o for o in stage.operands for c in "*?[{$`"):
        return None
    return max(1, len(stage.operands))


def blocks(result: str) -> list[set[str]] | None:
    """The result's long listings of a directory in order, each as the names it lists, lower-cased; None where the tool
    cut the output or a listing names its directory (`dir:`), so listings cannot be matched to the commands that
    printed them."""
    if TOOL_NOTE.search(result):
        return None
    out: list[set[str]] = []
    current: set[str] | None = None
    for line in result.splitlines():
        text = line.strip()
        if TOTAL.fullmatch(text):
            current = set()
            out.append(current)
            continue
        if LISTING_HEADER.fullmatch(text):
            return None
        entry = ENTRY.match(line) if current is not None else None
        if entry is None:
            current = None
            continue
        current.add(entry.group(1).strip().strip("'\"").lower())
    return out


def listing_steps(all_steps: list[Step]) -> list[int] | None:
    """The steps that print a long listing into the result, in order, where each prints one listing of one directory
    whole: one operand or none, no -d, -R or glob, its output into no pipe; None where one does not."""
    out = []
    for k, step in enumerate(all_steps):
        stage = step.command
        if not long_listing(stage) or step.redirected:
            continue
        if step.piped or "d" in listing_flags(stage) or most_listings(step) != 1:
            return None
        out.append(k)
    return out


def resolve(path: str, base: str | None) -> str | None:
    """The path, lower-cased, as an absolute path, or under "@", the directory the call started in; None where it is
    relative and the base is unknown, or climbs out of "@"."""
    if path.startswith("/"):
        return posixpath.normpath(path).lower()
    if base is None:
        return None
    joined = posixpath.normpath(posixpath.join(base, path)) if path else base
    return joined.lower() if joined.startswith(("/", "@")) else None


class Reading:
    """One call's command and result read together: its steps, its error lines and their owners, the steps its error
    lines and echoes show ran, the output lines no error, complaint or literal echo accounts for, and the steps that may
    print them."""

    def __init__(self, cmd: str, result: str):
        self.result = result
        self.steps = steps(cmd)
        self.entries = error_entries(result, self.steps)
        self.errors = [(line, owners) for _, line, owners in self.entries]
        self.owners: set[int] = set().union(*(owners for _, owners in self.errors)) if self.errors else set()
        self.done = ran(self.errors, self.steps, result)
        error_rows = {n for n, _, _ in self.entries}
        said = {s.said for s in self.steps if s.said}
        self.lines = [line.strip() for line in result.splitlines()]
        self.ordinary = [line for n, line in enumerate(self.lines) if line and n not in error_rows and line not in said
                         and not COMPLAINT.search(line) and not TOOL_NOTE.search(line)]
        traced = any(s.stage.program == "set" and "x" in listing_flags(s.stage) for s in self.steps)
        self.speakers = None if traced else {k for k, s in enumerate(self.steps) if s.speaks and not s.said}

    def shows_one_of(self, group: set[int]) -> bool:
        """Whether the result shows that a step of `group` ran: an error line only its steps own, an echo of its
        literal words that no other echo says, output that only its steps could print (every step that may print stands
        in it), or more long listings than the steps outside it can print."""
        if not group:
            return False
        if any(owners and owners <= group for _, owners in self.errors):
            return True
        said = Counter(s.said for s in self.steps if s.said)
        if any(k in group and s.said and said[s.said] == 1 and s.said in self.lines for k, s in enumerate(self.steps)):
            return True
        if self.ordinary and self.speakers and self.speakers <= group:
            return True
        totals = sum(1 for line in self.lines if TOTAL.fullmatch(line))
        listers = [k for k, s in enumerate(self.steps) if long_listing(s.command)]
        if not totals or not any(k in group for k in listers):
            return False
        outside = [most_listings(self.steps[k]) for k in listers if k not in group]
        return None not in outside and totals > sum(outside)

    def proven(self, k: int) -> bool:
        """Whether a step that runs only once step k succeeded ran."""
        return any(k in self.steps[j].proves for j in self.done) or self.shows_one_of(
            {j for j, s in enumerate(self.steps) if k in s.proves})

    def blocked(self, k: int) -> bool | None:
        """Whether step k failed by the result, where no step proves it succeeded (proven): it printed an error line,
        or a step it waits on did, or a step that runs only once it did not succeed ran (printed an error, an echo of
        its words: `cp a b 2>/dev/null || echo SHUT`, or output only it could print). A step that sends its errors to
        /dev/null fails where the same program printed one elsewhere in the call (`git show a:f > x 2>/dev/null` beside
        a `git log` that found no repository); else its output cannot tell (None)."""
        step = self.steps[k]
        if self.owners & {k, *step.waits_on} or any(r in self.owners or echoed(self.steps[r], self.result)
                                                    for r in step.rescued_by) \
                or self.shows_one_of(set(step.rescued_by)):
            return True
        if step.silent:
            return True if any(self.steps[o].stage.program == step.stage.program for o in self.owners) else None
        return False

    def cwd(self, k: int) -> str | None:
        """The directory step k runs in, as the cd commands before it in its groups say (resolve): None after a cd
        that may not have run (one that waits on others or runs only once another failed, unless step k's running
        proves it), that printed an error, or whose target the call cannot tell ($, ~, -)."""
        here: str | None = "@"
        scope = self.steps[k].scope
        rescuers = {r for s in self.steps for r in s.rescued_by}
        for j in range(k):
            step = self.steps[j]
            if step.command.program != "cd" or scope[:len(step.scope)] != step.scope:
                continue
            ops = step.command.operands
            target = ops[0] if ops else ""
            certain = j in self.steps[k].proves or (not step.waits_on and j not in rescuers)
            if not certain or j in self.owners or not target or target == "-" or any(c in target for c in "$~`*?"):
                here = None
            else:
                here = resolve(target, here)
        return here

    def place(self, path: str, copiers: list[int]) -> str | None:
        """The directory the copy lies in: its parent where its path is absolute, else its path's directory under the
        directory its last maker ran in."""
        name = path.rstrip(";,)").lower()
        if name.startswith("/"):
            return str(PurePosixPath(name).parent)
        parent = str(PurePosixPath(name).parent)
        return resolve("" if parent == "." else parent, self.cwd(max(copiers))) if copiers else None

    def absent(self, path: str, copiers: list[int]) -> bool:
        """Whether a long listing of the copy's directory, printed after every step that makes it with no step between
        that removes, moves or rewrites it (touches), lists the directory without it: `git checkout agent-014 2>&1 |
        tail -3 && ls -la` after an aborted checkout. Listings match their commands by order, only where each prints
        one (listing_steps) and the result has as many."""
        found, listers = blocks(self.result), listing_steps(self.steps)
        if not copiers or found is None or listers is None or len(found) != len(listers):
            return False
        base = PurePosixPath(path.rstrip(";,)").lower()).name
        where = self.place(path, copiers)
        if not base or base in (".", "..") or where is None:
            return False
        last = max(copiers)
        for block, k in zip(found, listers):
            stage = self.steps[k].command
            if k <= last or (base.startswith(".") and not set("aA") & set(listing_flags(stage))):
                continue
            if resolve(stage.operands[0] if stage.operands else "", self.cwd(k)) != where:
                continue
            if any(touches(self.steps[j], base, where) for j in range(last + 1, k)):
                continue
            if base not in block:
                return True
        return False

    def emptied(self, path: str, copiers: list[int]) -> bool:
        """Whether a listing gives the copy zero bytes where every listing of the call comes after its makers."""
        listers = [k for k, s in enumerate(self.steps) if s.command.program == "ls"]
        return bool(copiers) and bool(listers) and min(listers) > max(copiers) and listed(path, self.result) is False


def touches(step: Step, base: str, where: str) -> bool:
    """Whether the step may remove, move or rewrite the file `base` in the directory `where`: a git verb that writes the
    working tree, a changer or copier that names the file or the directory, an interpreter that names the file."""
    stage = step.command
    if stage.program == "git":
        return git_verb(stage) in GIT_WORKTREE
    named = {PurePosixPath(a.rstrip("/")).name.lower() for a in [*stage.operands, *stage.writes] if a}
    if stage.program in CHANGERS | COPY_PROGRAMS | {"rmdir"}:
        return base in named or PurePosixPath(where).name in named
    if step.interpreter:
        return base in (step.text + "\n" + step.body).lower()
    return base in {PurePosixPath(w).name.lower() for w in stage.writes}


def missing_after(path: str, copiers: list[int], errors: list[tuple[str, set[int]]]) -> bool:
    """Whether a later command found the copy missing: an error line that says its path, or a directory it lies in,
    does not exist, owned by no step or by one after every step that makes it (`git archive ... 2>/dev/null | tar -x;
    python -c "open('R.zip')"` and python's FileNotFoundError naming R.zip; `ls /tmp/out` after a convert into it)."""
    name = path.rstrip(";,)")
    places = [str(p) for p in PurePosixPath(name).parents if len(p.parts) > 2] if name.startswith("/") else []
    return any(MISSING.search(line) and (names_file(line.lower(), name) or any(names_dir(line.lower(), place)
                                                                              for place in places))
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


def later_absent(base: str, where: str, cmd: str, result: str) -> bool:
    """Whether a later call's long listing of the absolute directory `where` lists it without the file `base`."""
    call = Reading(cmd, result)
    found, listers = blocks(result), listing_steps(call.steps)
    if found is None or listers is None or len(found) != len(listers):
        return False
    for block, k in zip(found, listers):
        stage = call.steps[k].command
        if base.startswith(".") and not set("aA") & set(listing_flags(stage)):
            continue
        if any(touches(call.steps[j], base, where) for j in range(k)):
            return False
        if resolve(stage.operands[0] if stage.operands else "", call.cwd(k)) == where and base not in block:
            return True
    return False


def later_verdict(path: str, later: Iterable[tuple[str, str]], redirect: bool = False,
                  sized: bool = True, where: str | None = None) -> bool | None:
    """What the agent's later calls (command, result) say of a copy its own call cannot tell of: the first that names
    it, or its directory, in a listing (listed), a reader's word that it is no archive (CORRUPT), a line that says it
    is missing, for a copy no redirect made, a line that is its name alone (`ls`), or a long listing of its absolute
    directory (its parent, or `where` for a relative path) without it, until a call that may remove or rewrite it
    (touches). The search stops at a call that writes, removes, moves or converts into a file of its name, since what
    follows speaks of that file. None where none tells."""
    name = path.rstrip(";,)").lower()
    base, parent = PurePosixPath(name).name, str(PurePosixPath(name).parent)
    if not base:
        return None
    place = parent if name.startswith("/") else where
    deep = place is not None and place.startswith("/") and len(PurePosixPath(place).parts) > 2
    for cmd, result in later:
        lowered = cmd.lower()
        names_it = names_file(lowered, base) or (name.startswith("/") and len(PurePosixPath(parent).parts) > 2
                                                 and names_dir(lowered, parent))
        if not names_it:
            if deep and names_dir(lowered, place):
                if later_absent(base, place, cmd, result):
                    return False
                if any(touches(s, base, place) for s in steps(cmd)):
                    deep = False
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
        missing_place = name if name.startswith("/") else base
        if any(MISSING.search(line) and names_file(line.lower(), missing_place) for line in result.splitlines()):
            return False
        if deep:
            if later_absent(base, place, cmd, result):
                return False
            if any(touches(s, base, place) for s in steps(cmd)):
                deep = False
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
    unzip) whose text or here-document names it; else, for a relative path, the git commands that write the working
    tree (GIT_WORKTREE). Empty where no step makes it: a reader of the copy never made it."""
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
    maker = next(([k] for k, s in enumerate(all_steps) if (s.interpreter or s.stage.program in MAKERS)
                  and base in (s.text + "\n" + s.body).lower()), [])
    if maker or path.startswith("/"):
        return maker
    return [k for k, s in enumerate(all_steps) if s.stage.program == "git" and git_verb(s.stage) in GIT_WORKTREE]


def made_by_redirect(path: str, all_steps: list[Step]) -> bool:
    """Whether a step writes the copy through a redirect, which leaves an empty file where the command fails."""
    base = PurePosixPath(path.rstrip(";,)")).name.lower()
    return any(base in {PurePosixPath(w).name.lower() for w in s.stage.writes} for s in all_steps)


def into_own_file(path: str, all_steps: list[Step]) -> bool:
    """Whether a step that writes the copy through a redirect sends its errors into it too."""
    base = PurePosixPath(path.rstrip(";,)")).name.lower()
    return any(s.into_file and base in {PurePosixPath(w).name.lower() for w in s.stage.writes} for s in all_steps)


def refused(tool_error: str | None) -> bool:
    """Whether the tool's error says the command never ran: any tool error but a timeout."""
    return tool_error is not None and TIMED_OUT.match(tool_error) is None


def copy_verdicts(cmd: str, result: str, made: set[str], timed_out: bool = False) -> dict[str, bool | None]:
    """Each copy of `made`: True where it landed, False where its making failed, None where the call's output cannot
    tell. It landed where a step that makes it is proven to have succeeded (Reading.proven). Else it failed where a
    later command of the call found it missing, a reader found it no archive, a long listing of its directory after it
    leaves it out (Reading.absent), a listing after it gives it zero bytes, or no step that makes it succeeded
    (Reading.blocked). Where the output cannot tell (a maker that sends its errors to /dev/null, a copy no step names,
    a call that timed out, where no error is no proof), a listing decides (listed), then a line that names it unless a
    redirect made it; else None. An empty result fails no copy whose makers speak."""
    all_steps = steps(cmd)
    if not result.strip():
        return {path: None if timed_out or any(all_steps[k].silent for k in copier_steps(path, all_steps)) else True
                for path in made}
    call = Reading(cmd, result)
    out: dict[str, bool | None] = {}
    for path in made:
        copiers = copier_steps(path, all_steps)
        if any(call.proven(k) for k in copiers):
            out[path] = True
            continue
        verdicts = {call.blocked(k) for k in copiers}
        if copiers and (missing_after(path, copiers, call.errors) or corrupt_after(path, copiers, result, all_steps)
                        or call.absent(path, copiers) or call.emptied(path, copiers)):
            out[path] = False
        elif False in verdicts and not timed_out:
            out[path] = True
        elif not copiers or None in verdicts or False in verdicts:
            seen = listed(path, result, not into_own_file(path, all_steps))
            if seen is None and not made_by_redirect(path, all_steps) and shown(path, result, call.errors):
                seen = True
            out[path] = seen
        else:
            out[path] = False
    return out


def failed_copies(cmd: str, result: str, made: set[str], whole_failed: Callable[[str], bool],
                  later: Iterable[tuple[str, str]] = (), tool_error: str | None = None) -> set[str]:
    """The copies of `made` whose making failed (copy_verdicts). A call the tool refused (`tool_error`, refused) made
    none. A copy the call's output cannot tell of is decided by the agent's later calls (later_verdict), else, for a
    call that timed out, it failed; else by the caller's reading (whole_failed) of the output that the call's other
    steps do not own (own_output), or of the whole result where no step names the copy. An empty output that no
    later call speaks to fails nothing."""
    if refused(tool_error):
        return set(made)
    timed_out = tool_error is not None
    later = list(later)
    call = Reading(cmd, result)
    all_steps = call.steps
    failed = set()
    for path, verdict in copy_verdicts(cmd, result, made, timed_out).items():
        copiers = copier_steps(path, all_steps)
        if verdict is None:
            where = call.place(path, copiers) if not path.startswith("/") else None
            verdict = later_verdict(path, later, made_by_redirect(path, all_steps), not into_own_file(path, all_steps),
                                    where)
        if verdict is None and timed_out:
            verdict = False
        if verdict is None:
            keep = {j for k in copiers for j in (k, *all_steps[k].waits_on, *all_steps[k].rescued_by)}
            read = own_output(result, call.entries, keep) if copiers else result
            verdict = not (read.strip() and whole_failed(read))
        if not verdict:
            failed.add(path)
    return failed
