"""Random content a replay cannot draw again: the certificates and keys a call makes with openssl or crypto/rand. Its
bytes are new on every draw, so a replay that makes them anew ends with other files, other commit ids and other
diffs, and no commit date gives the recorded id. The swap puts the recorded bytes in place of the drawn ones, in the
agent's machine and in the call's output, right after the call and before its commits are dated.

    swap = CertificateSwap(run.path / "swe" / "diffs", {agent: [its diff files]}, record_text)
    output, swapped = swap.apply(box, agent, call.result, output, command)    # swapped: None, or what changed

Two sources give the recorded bytes. The call's recorded output, where it shows a PEM block (BEGIN ... END) that the
replayed output shows with other lines: each replayed line gives way to the recorded one. And the agent's own stored
diffs (its graded tree, its own diff, its branch on the team remote), for a file nothing printed: a PEM file at a path
those diffs add, holding as many blocks of the same kinds and line counts but other lines, gets the stored lines.
Another agent's diffs never give a swap: its file is its own work, not a draw of this agent's.

A line is swapped only when it was drawn: it appears nowhere in the record (no call's arguments or output, so no agent
typed it and no result showed it) and in no tree the agent's repository holds at a ref tip (the base, the planted
pieces, the branches): content an agent typed or took from the repository is the same on every replay and is never
swapped. The lines are swapped in every small file under /app, /tmp and /root of the agent's machine (a copy the agent
made, a block inside a YAML file), keeping each file's times, and in the output. A block whose shape differs (other
kinds, other line counts) is left alone.

The swap is opt-in (`python -m src replay --pem`): it changes what the replay's machines hold, and replay.json lists
every swap. Pure apart from apply, which runs two scripts in the agent's machine.
"""
from __future__ import annotations

import json
import re
import shlex
from pathlib import Path

PEM = re.compile(r"-----BEGIN ([A-Z0-9 ]+)-----\n([\s\S]*?)-----END \1-----")
"""A PEM block: its kind (CERTIFICATE, PRIVATE KEY, ...) and its base64 lines."""
LINE_MIN = 16
"""The shortest line swapped: a base64 line this long is never drawn twice, so it names its file's block alone."""
TRIGGER = re.compile(r"BEGIN|openssl|\.pem\b|\.crt\b|\.key\b|GenerateKey|x509|certificate", re.I)
"""A call that may have drawn a certificate or a key: only then are the stored files read."""
ROOTS = ("/app", "/tmp", "/root")
SKIP_DIRS = (".git", "go", ".cache", "node_modules")
FILE_MAX = 1_000_000

SWAP_SCRIPT = r"""
import json, os, sys
pairs, roots, skip, size = json.load(sys.stdin)
changed = []
for root in roots:
    for folder, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in skip]
        for name in files:
            path = os.path.join(folder, name)
            try:
                if os.path.islink(path) or os.path.getsize(path) > size:
                    continue
                with open(path, "rb") as handle:
                    data = handle.read()
            except OSError:
                continue
            new = data
            for old, recorded in pairs:
                new = new.replace(old.encode(), recorded.encode())
            if new != data:
                times = os.stat(path)
                with open(path, "wb") as handle:
                    handle.write(new)
                os.utime(path, ns=(times.st_atime_ns, times.st_mtime_ns))
                changed.append(path)
print(json.dumps(changed))
"""
"""Each (drawn line, recorded line) pair on stdin swapped in every small file under the roots; prints the files
changed. Runs in the agent's machine with its python3."""
READ_SCRIPT = r"""
import json, os, sys
out = {}
for path in json.load(sys.stdin):
    try:
        with open(os.path.join("/app", path), encoding="utf-8", errors="replace") as handle:
            out[path] = handle.read()
    except OSError:
        pass
print(json.dumps(out))
"""
"""The text of each path on stdin (relative to /app) that exists in the agent's machine."""
IN_REPOSITORY_SCRIPT = r"""
import json, subprocess, sys
lines = json.load(sys.stdin)
git = ["git", "-C", "/app"]
tips = sorted(set(subprocess.run(git + ["for-each-ref", "--format=%(objectname)"], capture_output=True,
                                 text=True).stdout.split()) | {"HEAD"})
patterns = [arg for line in lines for arg in ("-e", line)]
out = subprocess.run(git + ["grep", "-F", "-h", "-o", *patterns, *tips, "--"], capture_output=True, text=True).stdout
print(json.dumps(sorted(line for line in lines if line in out)))
"""
"""Those of the lines on stdin that a tree at a ref tip of the agent's repository (or HEAD) holds."""


def blocks(text: str) -> list[tuple[str, list[str]]]:
    """The PEM blocks of a text in order: (kind, base64 lines)."""
    return [(found.group(1), found.group(2).splitlines()) for found in PEM.finditer(text)]


def line_pairs(drawn: list[tuple[str, list[str]]], recorded: list[tuple[str, list[str]]]) -> list[tuple[str, str]]:
    """The swaps of two texts that hold as many blocks, block by block of the same kind and line count, where a block
    differs in a line of LINE_MIN or more: the whole block's body first (drawn, recorded), which takes its short lines
    too (an ECDSA signature's last line), then each long line that differs, which reaches a copy of the block at
    another indent. Only a line's content is the recorded one: each keeps the drawn line's indent, since the stored
    file's indent is its final one and the agent may indent the block again later. [] where their shapes differ."""
    if not drawn or len(drawn) != len(recorded):
        return []
    whole, lines_ = [], []
    for (kind, lines), (recorded_kind, recorded_lines) in zip(drawn, recorded):
        if kind != recorded_kind or len(lines) != len(recorded_lines):
            continue
        kept = [indent_of(a) + b.strip() for a, b in zip(lines, recorded_lines)]
        long = [(a, b) for a, b in zip(lines, kept) if a != b and len(a.strip()) >= LINE_MIN]
        if long:
            whole.append(("\n".join(lines), "\n".join(kept)))
            lines_ += long
    return list(dict.fromkeys(whole + lines_))


def indent_of(line: str) -> str:
    return line[:len(line) - len(line.lstrip())]


def long_lines(text: str) -> list[str]:
    """A drawn text's lines of LINE_MIN or more, stripped of a YAML block's indent: what the gates judge it by."""
    return [line.strip() for line in text.splitlines() if len(line.strip()) >= LINE_MIN]


def changed_lines(pairs: list[tuple[str, str]]) -> int:
    """The distinct lines the pairs change."""
    return len({(a, b) for old, new in pairs for a, b in zip(old.splitlines(), new.splitlines()) if a != b})


def output_pairs(recorded: str, replayed: str) -> list[tuple[str, str]]:
    """The swaps the recorded output gives: its PEM blocks against the replayed output's."""
    return line_pairs(blocks(replayed), blocks(recorded))


def stored_blocks(diffs_dir: Path, names: list[str]) -> dict[str, list[tuple[str, list[str]]]]:
    """Each file the stored diffs `names` (files under diffs_dir, in that order) add or change that holds PEM blocks,
    by its path under /app: the blocks of its added lines, from the first of them that holds the file."""
    out: dict[str, list[tuple[str, list[str]]]] = {}
    for diff in [Path(diffs_dir) / name for name in names if (Path(diffs_dir) / name).is_file()]:
        for section in re.split(r"(?m)^diff --git ", diff.read_text(errors="replace"))[1:]:
            path = re.match(r"a/\S+ b/(\S+)", section)
            if not path or path.group(1) in out:
                continue
            added = "\n".join(line[1:] for line in section.splitlines()
                              if line.startswith("+") and not line.startswith("+++"))
            found = blocks(added + "\n")
            if found:
                out[path.group(1)] = found
    return out


def stored_pairs(stored: dict[str, list[tuple[str, list[str]]]], files: dict[str, str]) -> list[tuple[str, str]]:
    """The swaps the stored diffs give: each file the agent holds at a stored path against the stored blocks."""
    pairs: list[tuple[str, str]] = []
    for path, text in files.items():
        pairs += line_pairs(blocks(text), stored.get(path, []))
    return list(dict.fromkeys(pairs))


def swapped(text: str, pairs: list[tuple[str, str]]) -> str:
    for old, recorded in pairs:
        text = text.replace(old, recorded)
    return text


def unrecorded(pairs: list[tuple[str, str]], record: str) -> list[tuple[str, str]]:
    """The pairs none of whose drawn long lines appears in the record's text."""
    return [(drawn, kept) for drawn, kept in pairs if not any(line in record for line in long_lines(drawn))]


class CertificateSwap:
    def __init__(self, diffs_dir: Path, own_diffs: dict[str, list[str]], record: str) -> None:
        """own_diffs: each agent's own stored diff files, by name; record: every call's arguments and output."""
        self.stored = {agent: stored_blocks(diffs_dir, names) for agent, names in own_diffs.items()}
        self.record = record

    def apply(self, box, agent: str, recorded: str, output: str, command: str) -> tuple[str, dict | None]:
        """The swaps for one call in the agent's machine and its output: (output, None) when nothing was swapped,
        else (output with the recorded lines, {"source": [...], "lines", "files"})."""
        sources: dict[str, list[tuple[str, str]]] = {}
        if recorded != output and "-----BEGIN" in recorded:
            sources["output"] = unrecorded(output_pairs(recorded, output), self.record)
        stored = self.stored.get(agent) or {}
        if stored and TRIGGER.search(command + recorded + output):
            read = box.exec(agent, f"python3 -c {shlex.quote(READ_SCRIPT)}", stdin=json.dumps(sorted(stored)).encode())
            found = stored_pairs(stored, json.loads(read.stdout.decode() or "{}")) if read.rc == 0 else []
            sources["stored diffs"] = unrecorded(found, self.record)
        pairs = list(dict.fromkeys(p for found in sources.values() for p in found))
        if pairs:
            held = box.exec(agent, f"python3 -c {shlex.quote(IN_REPOSITORY_SCRIPT)}",
                            stdin=json.dumps(sorted({ln for drawn, _ in pairs for ln in long_lines(drawn)})).encode())
            if held.rc != 0:
                raise RuntimeError(f"the repository check failed in {agent}: "
                                   f"{held.stderr.decode(errors='replace')[-500:]}")
            kept = set(json.loads(held.stdout.decode()))
            pairs = [(drawn, new) for drawn, new in pairs if not any(ln in kept for ln in long_lines(drawn))]
            sources = {name: [p for p in found if p in pairs] for name, found in sources.items()}
        if not pairs:
            return output, None
        pairs.sort(key=lambda pair: "\n" not in pair[0])            # whole blocks before single lines
        done = box.exec(agent, f"python3 -c {shlex.quote(SWAP_SCRIPT)}",
                        stdin=json.dumps([pairs, ROOTS, SKIP_DIRS, FILE_MAX]).encode())
        if done.rc != 0:
            raise RuntimeError(f"the certificate swap failed in {agent}: {done.stderr.decode(errors='replace')[-500:]}")
        return swapped(output, pairs), {"source": [name for name, found in sources.items() if found],
                                        "lines": changed_lines(pairs), "files": json.loads(done.stdout.decode())}
