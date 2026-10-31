"""The shared reader of an exported run: every tool loads its runs through `load_runs` and writes through
`write_summary`.

    [run] = load_runs(["riemann_minimal"], "graph")  # or a path such as runs/smoke/complete; None when one is missing
    run.cheated  # {"k7q2m": False, "t4x9n": True, "p8s3z": True}
    run.agent_steps("k7q2m")[0].round  # 1
    run.reads("k7q2m")[0].posts  # [("p8s3z", 1)]
    write_summary(run, "graphs", {"nodes": 3})  # runs/riemann_minimal/tools/graphs/summary.json

A run directory holds run.json, episodes.jsonl, judge.jsonl, trajectories/<episode>/transcript.json and
communication/<sample>.json, read through src/measures/run_directory.py RunDir. The turn rules' timeline is run.json
rules.timeline. The eval log named by run.json holds every board_read result as the agent saw it. They are cached in
runs/<study>/<name>/tools/board_reads.json the first time a run is loaded, so later tools need not reopen the log. A run
whose log is gone reads the cache when there is one, else the transcripts answer.

Rounds come from the timeline. The k-th agent step of an agent's ATIF transcript is the k-th activation of that agent
in the timeline, and the round is the integer after "r" in the activation's step id ("r3", "r3-free1", "r12-v4").
A board post's round is the round of its sender's activation at the post's turn.
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from inspect_ai.log import read_eval_log

from src.configuration import settings
from src.measures.run_directory import RunDir
from src.utils.json_files import read_json

# The registry's domain key of each CheatBench task (src/domains/__init__.py BY_TASK), the key of
# instruction_levels.yaml vars. src.domains imports ORBIT, which tools/.venv lacks, so the pairs are written here.
DOMAIN_KEYS = {"openmath_agent": "openmath", "software_engineering": "swe", "knowledge_work": "knowledge_work",
               "subset_sum": "subset_sum", "prime_factorization": "prime_factorization",
               "protein_design": "protein_design", "creative_writings": "creative_writing", "chess": "chess"}
# The board's channel id (src/swarm/turn_rules/, which imports ORBIT).
BOARD_ID = "board"
BOARD_READ_TOOLS = ("board_read",)
UNREADABLE = (FileNotFoundError, ValueError, KeyError)   # what reading a missing or malformed run raises
ROUND_ID = re.compile(r"^r(\d+)")
READ_POST = re.compile(r"^\[(\S+) #(\d+)\]", re.MULTILINE)


@dataclass(frozen=True)
class Post:
    """One post on the board."""

    number: int  # its position on the board, oldest first: the n of "[p8s3z #n]" and "posted #n"
    sequence: int  # ORBIT's sequence number across all communication events
    sender: str
    recipients: list[str]
    content: str
    turn: int | None  # the timeline's turn counter when it was posted
    round: int | None  # the round of the sender's activation at that turn; None without a timeline
    sample: str  # the communication file's sample, such as "riemann#1"


@dataclass(frozen=True)
class Step:
    """One step of an agent's ATIF transcript."""

    index: int  # position in trajectory.steps, from 0
    step_id: int  # the ATIF step_id
    source: str  # "system", "user" or "agent"
    text: str  # the step's message
    tool_calls: list[tuple[str, dict]]  # (function name, arguments) in call order
    call_ids: list[str]  # the tool_call_id of each call, in the same order
    observations: list[str]  # the content of each tool result, in order
    result_ids: list[str]  # the source_call_id of each result, in the same order
    reasoning: str | None
    activation: int | None  # k for the k-th agent step; None for system and user steps
    schedule_step: str | None  # the timeline's step id of that activation ("r3", "r3-free1", "plan1")
    turn: int | None  # the timeline's turn counter of that activation
    round: int | None  # the round of that activation; None when the timeline does not cover it
    extra: dict  # the ATIF extra block, such as {"attached_by": "swarm_turn_rules"}


@dataclass(frozen=True)
class Read:
    """One board_read call by one agent."""

    agent: str
    round: int | None
    step: int  # the index of the agent step in trajectory.steps
    activation: int
    call_id: str
    posts: list[tuple[str, int]]  # (sender, board number) of every post returned, oldest first
    text: str  # the tool result as the agent saw it


@dataclass(frozen=True)
class TraceEntry:
    """One event of the merged trace: a board post or an agent step."""

    round: int | None
    turn: int | None
    agent: str
    role: str
    kind: str  # "post" or "step"
    text: str  # the post's content, or the step's message
    tools: list[str]  # the names of the step's tool calls; empty for a post
    calls: list[str]  # each call rendered as name(arguments as JSON); empty for a post
    ref: int  # the step's index in trajectory.steps, or the post's board number

    @property
    def rendered(self) -> str:
        """The entry as one line of text: "post: <text>", or "step: <calls joined by '; '> | <text>"."""
        if self.kind == "post":
            return f"post: {self.text}"
        return f"step: {'; '.join(self.calls)} | {self.text}"


@dataclass(frozen=True)
class Run:
    """One exported run directory, read once."""

    name: str  # the path under runs/, such as "riemann_minimal" or "smoke/complete"
    directory: Path
    run_json: dict
    task: str  # run.json task: CheatBench's task name, such as openmath_agent
    domain: str  # its registry key, such as openmath
    model: str  # the agents' Inspect model string
    agents: list[str]
    roles: dict[str, str]  # agent -> worker, regulator or disregulator
    config: dict  # run.json config (the SwarmConfig dump)
    eval_log: Path | None  # the .eval file; None when run.json names none or it is gone
    judge: dict[str, dict]  # agent -> judge.jsonl row
    cheated: dict[str, bool | None]  # agent -> the judge's verdict; None when the judge gave none
    episodes: dict[str, dict]  # agent -> episodes.jsonl record
    board: list[Post]  # every board post, by sample then board number
    transcripts: dict[str, dict]  # agent -> the transcript.json document
    rounds: int  # run.json rules.rounds; 0 for a run without turn rules
    timeline: list[dict]  # run.json rules.timeline entries {turn, agent, activation, step, tools, charge, ...}
    exploit: dict  # the domain's vars plus "access" (run.json access) and "holds" {agent: items}
    board_reads: dict[str, str] = field(default_factory=dict)  # tool call id -> board_read result, from the log

    def steps(self, agent: str) -> list[Step]:
        """Every step of the agent's transcript, with the round of each agent step."""
        transcript = self.transcripts.get(agent)
        if transcript is None:
            return []
        activations = {entry.get("activation"): entry for entry in self.timeline if entry.get("agent") == agent}
        steps, activation = [], 0
        for index, raw in enumerate(transcript["trajectory"]["steps"]):
            entry = None
            if raw.get("source") == "agent":
                activation += 1
                entry = activations.get(activation)
            calls = raw.get("tool_calls") or []
            results = (raw.get("observation") or {}).get("results") or []
            schedule_step = entry.get("step") if entry else None
            steps.append(Step(
                index=index,
                step_id=raw.get("step_id", index + 1),
                source=raw.get("source", ""),
                text=message_text(raw.get("message")),
                tool_calls=[(call.get("function_name", ""), call.get("arguments") or {}) for call in calls],
                call_ids=[call.get("tool_call_id", "") for call in calls],
                observations=[message_text(result.get("content")) for result in results],
                result_ids=[result.get("source_call_id", "") for result in results],
                reasoning=raw.get("reasoning_content"),
                activation=activation if raw.get("source") == "agent" else None,
                schedule_step=schedule_step,
                turn=entry.get("turn") if entry else None,
                round=round_of(schedule_step),
                extra=raw.get("extra") or {},
            ))
        return steps

    def agent_steps(self, agent: str) -> list[Step]:
        """The agent's own steps: one per activation."""
        return [step for step in self.steps(agent) if step.source == "agent"]

    def reads(self, agent: str) -> list[Read]:
        """Every board_read call of the agent, with the posts it returned."""
        reads = []
        for step in self.agent_steps(agent):
            for position, (name, _arguments) in enumerate(step.tool_calls):
                if name not in BOARD_READ_TOOLS:
                    continue
                call_id = step.call_ids[position]
                text = self.board_reads.get(call_id)
                if text is None:
                    text = observation_for(step, call_id, position)
                reads.append(Read(agent=agent, round=step.round, step=step.index, activation=step.activation,
                                  call_id=call_id, posts=parse_read(text, self.agents), text=text))
        return reads

    def posts(self, agent: str) -> list[Post]:
        """The board posts the agent sent, oldest first."""
        return [post for post in self.board if post.sender == agent]


class RunPlace(Protocol):
    """Where a tool writes: a run's name and directory (a Run, or the contagion tool's synthetic board)."""

    name: str
    directory: Path


def load_runs(names: list[str | Path], tool: str) -> list[Run] | None:
    """Every named run, or None after printing why one cannot be read; the tool then exits 2 before any work."""
    try:
        return [load_run(name) for name in names]
    except UNREADABLE as error:
        print(f"{tool}: {error}", file=sys.stderr)
        return None


def load_run(name_or_path: str | Path) -> Run:
    """Read one run from a name under runs/ or a path to its directory."""
    directory = run_directory(name_or_path)
    stored = RunDir(directory)
    run_json = stored.run_json
    agents = list(run_json["agents"])
    episode_rows = stored.episodes()
    lone = lone_agent(agents, episode_rows)
    episodes = {agent_of(row["episode"], lone): row for row in episode_rows}
    judge = {agent_of(episode, lone): row for episode, row in stored.verdicts().items()}
    eval_log = eval_log_path(run_json.get("eval_log"))
    timeline = list((run_json.get("rules") or {}).get("timeline") or [])
    board_reads = board_read_results(directory, eval_log)
    task = run_json["task"]
    domain = DOMAIN_KEYS[task]
    return Run(
        name=run_name(directory),
        directory=directory,
        run_json=run_json,
        task=task,
        domain=domain,
        model=run_json.get("model", ""),
        agents=agents,
        roles=roles_of(agents, run_json, episodes),
        config=run_json["config"],
        eval_log=eval_log,
        judge=judge,
        cheated={agent: row.get("cheated") for agent, row in judge.items()},
        episodes=episodes,
        board=read_board(directory, timeline),
        transcripts=read_transcripts(directory, lone),
        rounds=int((run_json.get("rules") or {}).get("rounds") or 0),
        timeline=timeline,
        exploit=exploit_of(domain, run_json),
        board_reads=board_reads,
    )


def tools_dir(run: RunPlace, tool: str) -> Path:
    """runs/<study>/<name>/tools/<tool>/, created when missing."""
    directory = run.directory / "tools" / tool
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def write_summary(run: RunPlace, tool: str, payload: dict) -> Path:
    """Write runs/<study>/<name>/tools/<tool>/summary.json as {tool, run, written_at, **payload}."""
    reserved = {"tool", "run", "written_at"} & set(payload)
    if reserved:
        raise ValueError(f"the payload may not set {sorted(reserved)}")
    written_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    summary = {"tool": tool, "run": run.name, "written_at": written_at, **payload}
    path = tools_dir(run, tool) / "summary.json"
    path.write_text(json.dumps(summary, indent=2, default=str) + "\n")
    return path


def read_summary(run: Run, tool: str) -> dict | None:
    """The tool's summary.json for the run, or None when the tool has not written one."""
    return read_json(run.directory / "tools" / tool / "summary.json")


def merged_trace(run: Run) -> list[TraceEntry]:
    """Every board post and every agent step in chronological order: by round, then turn, a step before its post."""
    entries = []
    for agent in run.transcripts:
        role = run.roles.get(agent, "worker")
        for step in run.agent_steps(agent):
            entries.append(TraceEntry(
                round=step.round, turn=step.turn, agent=agent, role=role, kind="step", text=step.text,
                tools=[name for name, _arguments in step.tool_calls],
                calls=[f"{name}({json.dumps(arguments, ensure_ascii=False)})" for name, arguments in step.tool_calls],
                ref=step.index,
            ))
    for post in run.board:
        entries.append(TraceEntry(round=post.round, turn=post.turn, agent=post.sender,
                                  role=run.roles.get(post.sender, "worker"), kind="post", text=post.content,
                                  tools=[], calls=[], ref=post.number))
    return sorted(entries, key=trace_order)


def trace_order(entry: TraceEntry) -> tuple:
    """Sort key of the merged trace; entries without a round go last."""
    return (entry.round is None, entry.round or 0, entry.turn is None, entry.turn or 0,
            entry.kind == "post", entry.agent, entry.ref)


def round_of(schedule_step: str | None) -> int | None:
    """The round of a timeline step id: r3, r3-free1, r12-v4 give 3, 3, 12; plan<k> gives 0; None stays None."""
    if not schedule_step:
        return None
    if schedule_step.startswith("plan"):
        return 0
    match = ROUND_ID.match(schedule_step)
    return int(match.group(1)) if match else None


def parse_read(text: str, agents: list[str]) -> list[tuple[str, int]]:
    """The (sender, board number) of every post in a board_read result, oldest first.

    A line counts only when it starts with "[<sender> #<n>]" and the sender is an agent of the run, so a post whose
    own text quotes that pattern for someone else is not counted.
    """
    known = set(agents)
    return [(sender, int(number)) for sender, number in READ_POST.findall(text or "") if not known or sender in known]


def message_text(message) -> str:
    """A transcript message as text: a string as is, a list of content parts joined by newlines."""
    if message is None:
        return ""
    if isinstance(message, str):
        return message
    if isinstance(message, list):
        return "\n".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in message)
    return str(message)


def observation_for(step: Step, call_id: str, position: int) -> str:
    """The result of one tool call from the transcript: by source_call_id, else by position."""
    if call_id and call_id in step.result_ids:
        return step.observations[step.result_ids.index(call_id)]
    return step.observations[position] if position < len(step.observations) else ""


def run_directory(name_or_path: str | Path) -> Path:
    """The run's directory: the path itself when it holds a run.json, else runs/<study>/<name>."""
    candidates = [Path(name_or_path), settings.RUNS / str(name_or_path)]
    for candidate in candidates:
        if (candidate / "run.json").is_file():
            return candidate.resolve()
    raise FileNotFoundError(f"no run.json in {name_or_path} or {settings.RUNS / str(name_or_path)}")


def run_name(directory: Path) -> str:
    """The run's path under runs/, or its directory name when it lives elsewhere."""
    try:
        return "/".join(directory.relative_to(settings.RUNS.resolve()).parts)
    except ValueError:
        return directory.name


def lone_agent(agents: list[str], episode_rows: list[dict]) -> str | None:
    """The agent of a run with one agent, whose episode carries no "@agent" suffix."""
    if len(agents) != 1:
        return None
    named = [row.get("orbit_agent") for row in episode_rows if row.get("orbit_agent")]
    return named[0] if named else agents[0]


def agent_of(episode: str, lone: str | None) -> str:
    """The agent of an episode id: the part after "@", or the lone agent's name."""
    if "@" in episode:
        return episode.rsplit("@", 1)[1]
    return lone or episode


def roles_of(agents: list[str], run_json: dict, episodes: dict[str, dict]) -> dict[str, str]:
    """Each agent's hidden role: run.json swarm.roles, else the episode's role, else worker."""
    swarm_roles = (run_json.get("swarm") or {}).get("roles") or {}
    return {agent: swarm_roles.get(agent) or (episodes.get(agent) or {}).get("role") or "worker" for agent in agents}


def eval_log_path(value: str | None) -> Path | None:
    """The .eval file run.json names, relative to the repository root unless absolute; None when it is gone."""
    if not value:
        return None
    path = Path(value) if Path(value).is_absolute() else settings.ROOT / value
    return path if path.is_file() else None


def board_read_results(directory: Path, eval_log: Path | None) -> dict[str, str]:
    """The board_read results by tool call id, from the cache when it is as new as the eval log, else from the log
    (then cached); the cache alone when the log is gone, else {}."""
    cache = directory / "tools" / "board_reads.json"
    if eval_log is None:
        return read_json(cache) if cache.is_file() else {}
    if cache.is_file() and cache.stat().st_mtime >= eval_log.stat().st_mtime:
        return read_json(cache)
    results = read_log(eval_log)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(results, indent=1, ensure_ascii=False) + "\n")
    return results


def read_log(eval_log: Path) -> dict[str, str]:
    """Every board_read result of every sample by tool call id.

    A call id that occurs twice in the log (mock models reuse "c1") is left out, so the transcript answers for it.
    """
    log = read_eval_log(str(eval_log))
    results, seen = {}, {}
    for sample in log.samples or []:
        for event in sample.events:
            if event.event == "tool" and event.function in BOARD_READ_TOOLS:
                seen[event.id] = seen.get(event.id, 0) + 1
                results[event.id] = message_text(event.result)
    return {call_id: text for call_id, text in results.items() if seen[call_id] == 1}


def read_board(directory: Path, timeline: list[dict]) -> list[Post]:
    """Every post on the board channel of every communication/<sample>.json, numbered by board position."""
    turns = {(entry.get("agent"), entry.get("turn")): entry.get("step") for entry in timeline}
    posts = []
    for path in sorted((directory / "communication").glob("*.json")):
        messages = read_json(path).get("messages") or []
        board = [message for message in messages if message.get("channel_id", BOARD_ID) == BOARD_ID]
        for number, message in enumerate(sorted(board, key=lambda m: m.get("sequence", 0)), 1):
            turn = message.get("turn")
            posts.append(Post(number=number, sequence=message.get("sequence", 0), sender=message.get("sender", ""),
                              recipients=list(message.get("recipients") or []), content=message.get("content", ""),
                              turn=turn, round=round_of(turns.get((message.get("sender"), turn))),
                              sample=path.stem))
    return posts


def read_transcripts(directory: Path, lone: str | None) -> dict[str, dict]:
    """Each agent's transcript.json, keyed by the agent of its episode directory."""
    transcripts = {}
    for path in sorted((directory / "trajectories").glob("*/transcript.json")):
        transcripts[agent_of(path.parent.name, lone)] = read_json(path)
    return transcripts


def exploit_of(domain: str, run_json: dict) -> dict:
    """The domain's exploit vocabulary (instruction_levels.yaml vars), run.json access, and each agent's items."""
    variables = settings.load_instruction_levels()["vars"][domain]
    access = run_json.get("access") or {}
    holds = {agent: list(entry.get("holds") or []) for agent, entry in (access.get("agents") or {}).items()}
    return {**variables, "access": access, "holds": holds}
