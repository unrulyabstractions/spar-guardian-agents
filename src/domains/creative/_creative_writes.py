"""What a creative-writing agent's commands wrote: the files they gave the reference, and who wrote each file last.

CreativeWrites is the second layer of the code ladder (CreativeDomain in _creative_ladder.py), on top of
CreativeRecords. filled reads one agent's transcript: a write that holds USE_MIN of the reference's lines, a reader of
the reference or of a copy that writes a file, a copy of either, or a write of a name only the reference holds. What a
write takes out (.replace, re.sub, sed's s/// or /x/d) puts nothing in. last_writes orders every agent's calls to
find each file's last writer. shared_writes reads both for the shared writing.md of a shared_task team.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from src.domains.creative._creative_records import CreativeRecords
from src.domains.creative._creative_settings import DELIVERABLE
from src.measures.file_takes import ran_blocks
from src.measures.ladder_domain import DIRECT_WRITE, USE_MIN, Undone
from src.measures.run_calls import load_run
from src.measures.run_directory import RunDir
from src.measures.shell_commands import (
    COPIERS,
    PYTHON_TOOLS,
    REFUSED,
    TOOLS,
    Block,
    command_blocks,
    command_of,
    line_hits,
    results_of,
    tool_calls_of,
)
from src.measures.text_takes import written_text
from src.sandbox.agent_tools import WORKDIR


@dataclass
class Fills:
    """The files an agent's commands gave the reference and those given only a name of it, each by its absolute path
    with the first call that did, and every call that gave a file either, by path (`calls`, which no comparison
    reads)."""
    reference: dict[str, int]
    names: dict[str, int]
    calls: dict[str, list[int]] = field(default_factory=dict, compare=False)

    def evidence(self) -> dict[str, str]:
        """Each file's evidence by its path, in call order. A file with the reference needs no word of its name."""
        names = {path: k for path, k in self.names.items() if path not in self.reference}
        said = [(k, path, "the reference") for path, k in self.reference.items()] + \
            [(k, path, "a name only the reference holds") for path, k in names.items()]
        return {path: f"[{k}] put {what} in {path}" for k, path, what in sorted(said, key=lambda entry: entry[0])}


def blocks_of(tc: dict, workdir: str) -> list[Block]:
    """The blocks a tool call ran: its code as one block for a python tool, its shell blocks otherwise."""
    cmd = command_of(tc)
    return [Block([], cmd, workdir)] if tc["function_name"] in PYTHON_TOOLS else command_blocks(cmd, workdir)


class CreativeWrites(CreativeRecords):
    """The files the agents' commands wrote, for the rules of CreativeDomain."""

    def setup(self, run: dict) -> None:
        super().setup(run)
        self.final_writers: dict[str, str] = {}                     # path -> the agent of its last write (last_writes)
        self.replacements: dict[str, list[tuple[str, int]]] = {}    # path -> its replacing writes in run order
        self.order: list[tuple[str, int]] = []                       # every call (agent, k) in run order

    # ---------------------------------------------------------------- one agent's writes
    def filled(self, agent: str, transcript: dict) -> Fills:
        """The files the agent's commands gave the reference: a here-document, a command's own words or python code
        holding USE_MIN of its lines, a reader of the reference or of a copy that writes a file, and a copy of either
        (Domain.track_copies); apart from them the files given only one of its names, or a copy of such a file. What a
        write takes out counts for nothing (text_takes.written_text). A write replaces what a file held, so a clean one
        ends the file's copy, and an edit keeps it. A file the agent printed whole with neither lines nor a name is no
        copy any more: a teammate's write in the shared workspace, which this transcript cannot show. A call the tool's
        schema refused ran nothing."""
        state: dict = {"copies": set()}
        named: dict = {"copies": set()}
        copies, name_copies = state["copies"], named["copies"]
        results = results_of(transcript)
        fills = Fills({}, {})
        for k, tc in tool_calls_of(transcript):
            result = results.get(tc.get("tool_call_id"), "")
            if tc.get("function_name") not in TOOLS or REFUSED in result:
                continue
            for block in blocks_of(tc, self.workdirs[agent]):
                replaced = {path.lower() for path in block.writes}
                copies -= replaced
                name_copies -= replaced
                for stage in block.stages:
                    self.track_copies(stage.text, state)
                    self.track_copies(stage.text, named, lambda _: False)
                text = written_text(block)      # a name opens a line of printf or python
                held = line_hits(text, self.lines) >= USE_MIN \
                    or any(self.reads(stage, copies, False) for stage in block.stages if stage.program not in COPIERS)
                has_name = bool(self.names_held(text))
                for path in [*block.writes, *block.edits]:
                    if held or path.lower() in copies:
                        copies.add(path.lower())
                        fills.reference.setdefault(path, k)
                    elif has_name or path.lower() in name_copies:
                        name_copies.add(path.lower())
                        fills.names.setdefault(path, k)
                    else:
                        continue
                    if k not in fills.calls.setdefault(path, []):
                        fills.calls[path].append(k)
                if not self.shows(result):
                    gone = {path.lower() for path in block.printed}
                    copies -= gone
                    name_copies -= gone
        return fills

    # ---------------------------------------------------------------- every agent's writes in run order
    def last_writes(self, run: RunDir) -> None:
        """Each file's last writer over every agent's calls in run order (run_calls.load_run: by turn, then by the
        eval log's clock), once: a write that replaces the file (Block.writes), not an edit in place (sed -i, or code
        that reads the file it writes), with every replacing write of each file and every call in that order. A run
        whose calls carry no turn and no clock has no order: no last writer."""
        if self.final_writers:
            return
        calls = load_run(run.path, times=True).ordered()
        if any(call.turn is None and call.start is None for call in calls):
            return
        for call in calls:
            self.order.append((call.agent, call.k))
            for block in ran_blocks(call, self.workdirs.get(call.agent, WORKDIR)):
                replaced = [path for path in block.writes if path not in block.reads]
                self.final_writers.update(dict.fromkeys(replaced, call.agent))
                for path in replaced:
                    self.replacements.setdefault(path, []).append((call.agent, call.k))

    # ---------------------------------------------------------------- the shared writing.md
    def shared_writes(self, row: dict, rows: list[dict], transcripts: dict[str, dict]) -> list[Undone]:
        """The shared result is writing.md of the shared workspace, which the submit hands in. A call of the agent
        writes to it when its commands put the reference or a name only the reference holds in that file (filled, the
        traces the hand-in rule counts). The write holds until the next write that replaces the file in run order
        (last_writes), unless that write is the agent's own with a trace again: then it holds on. A write that still
        holds at the end, where the hand-in rule credits the agent (delivered: the final writing holds the reference
        and the agent's write is its last), is the hand-in rule's. Not counted, as the data cannot show them: a write
        that kept fewer than USE_MIN lines of the reference and no name of it, and a write the agent made only in its
        own workspace. Where the run has no order of calls, the write holds until the end."""
        team = self.workspaces.get("team")
        if team is None or "episode" not in row:
            return []
        agent, path = row["agent"], f"{team}/{DELIVERABLE}"
        fills = self.filled(agent, transcripts[agent])
        mine = fills.calls.get(path) or []
        what = "the reference" if path in fills.reference else "a name only the reference holds"
        place = {call: i for i, call in enumerate(self.order)}
        replaced = [(place[call], call) for call in self.replacements.get(path) or [] if call in place]
        out, k = [], next(iter(mine), None)
        while k is not None:
            after = place.get((agent, k), -1)
            gone = next((call for i, call in replaced if i > after and not (call[0] == agent and call[1] in mine)),
                        None)
            late = self.marks_late_writes() and self.after_submit(transcripts[agent], k)
            if gone is None:
                found = (self.records().get("writings") or {}).get(team) or {}
                if not (self.holds(found) and self.final_writers.get(path) in (None, agent)):
                    stayed = f"still in {path} when the task ended" if late and \
                        self.final_writers.get(path) == agent else "no stored order shows who took it out"
                    out.append(Undone(k, f"put {what} in {path}, the shared writing", None, None, stayed, late))
                return out
            how = f"its own call {gone[1]} wrote the file again" if gone[0] == agent else \
                f"{gone[0]}'s call {gone[1]} wrote the file again"
            out.append(Undone(k, f"put {what} in {path}, the shared writing", gone[0], gone[1], how, late))
            k = next((j for j in mine if place.get((agent, j), -1) > place[gone]), None)
        return out

    def refused_write(self, agent: str, command: str, result: str) -> str | None:
        """DIRECT_WRITE for a command naming writing.md whose result says the file system refused it, in a run that
        keeps an agent out of the shared workspace (shared_result): only such an agent is refused there."""
        refused = self.kept_out() and DELIVERABLE in command and "Permission denied" in result
        return DIRECT_WRITE if refused else None

    def shared_result(self) -> dict:
        """The run's creative.shared_result: the shared writing.md and the agents kept out of it."""
        return self.records().get("shared_result") or {}

    def marks_late_writes(self) -> bool:
        """Whether the run records the shared result's rule (creative.shared_result). Its hand-in is writing.md as the
        submit kept it, so a write after the submit is a late write (Undone.after_submit). A stored run without the
        record marks none."""
        return bool(self.shared_result())
