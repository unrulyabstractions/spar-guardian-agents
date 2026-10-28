"""The shared reader of an exported run: every tool loads a run through `load_run` and writes through `write_summary`.

    run = load_run("riemann_minimal")  # or a path such as runs/smoke/complete
    run.cheated  # {"a01": False, "a02": True, "a03": True}
    run.agent_steps("a01")[0].round  # 1
    run.reads("a01")[0].posts  # [("a03", 1)]
    write_summary(run, "graphs", {"nodes": 3})  # runs/riemann_minimal/tools/graphs/summary.json

A run directory holds run.json, episodes.jsonl, judge.jsonl, trajectories/<episode>/transcript.json and
communication/<sample>.json. The eval log named by run.json holds the turn rules' timeline in the sample store
(`TurnState:timeline`) and every board_read result. Both are cached under runs/<name>/tools/ (timeline.json and
board_reads.json) the first time a run is loaded, so later tools need not reopen the log. A run whose log is gone
reads the cache when there is one. A log or cache without a timeline falls back to the timeline that run.json keeps
under rules, and then to an empty one.

Rounds come from the timeline. The k-th agent step of an agent's ATIF transcript is the k-th activation of that agent
in the timeline, and the round is the integer after "r" in the activation's step id ("r3", "r3-free1", "r12-v4");
a planning step ("plan1") is round 0. A board post's round is the round of its sender's activation at the post's turn.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml
from inspect_ai.log import read_eval_log

from src.configuration.models import resolve

ROOT = Path(__file__).resolve().parents[1]
RUNS_DIR = ROOT / "runs"
INSTRUCTION_LEVELS = ROOT / "src" / "configuration" / "instruction_levels.yaml"
SKIPPED_PREFIXES = ("failed", "cancelled")
DOMAINS = {"openmath_agent": "openmath", "software_engineering": "swe", "knowledge_work": "knowledge_work",
           "subset_sum": "subset_sum", "prime_factorization": "prime_factorization",
           "protein_design": "protein_design"}
DOMAIN_PREFIXES = (("openmath", "openmath"), ("software", "swe"), ("swe", "swe"), ("knowledge", "knowledge_work"),
                   ("subset", "subset_sum"), ("prime", "prime_factorization"), ("factor", "prime_factorization"),
                   ("protein", "protein_design"), ("binder", "protein_design"))
TIMELINE_KEY = "TurnState:timeline"
BOARD_READ_TOOLS = ("board_read",)
BOARD_ID = "board"
ROUND_ID = re.compile(r"^r(\d+)")
READ_POST = re.compile(r"^\[(\S+) #(\d+)\]", re.MULTILINE)


@dataclass(frozen=True)
class Post:
    """One post on the board."""

    number: int  # its position on the board, oldest first: the n of "[a03 #n]" and "posted #n"
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


@dataclass(frozen=True)
class Run:
    """One exported run directory, read once."""

    name: str  # the path under runs/, such as "riemann_minimal" or "smoke/complete"
    directory: Path
    run_json: dict
    task: str  # run.json task: openmath_agent, software_engineering or knowledge_work
    domain: str  # "openmath", "swe" or "knowledge_work"
    model: str  # the agents' Inspect model string
    agents: list[str]
    roles: dict[str, str]  # agent -> worker, regulator or disregulator
    config: dict  # run.json config (the SwarmConfig dump), {} for runs before it
    eval_log: Path | None  # the .eval file; None when run.json names none or it is gone
    judge: dict[str, dict]  # agent -> judge.jsonl row
    cheated: dict[str, bool | None]  # agent -> the judge's verdict; None when the judge gave none
    episodes: dict[str, dict]  # agent -> episodes.jsonl record
    board: list[Post]  # every board post, by sample then board number
    transcripts: dict[str, dict]  # agent -> the transcript.json document
    rounds: int  # run.json rules.rounds; 0 for a run without turn rules
    timeline: list[dict]  # TurnState:timeline entries {turn, agent, activation, step, tools, charge, ...}
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


def load_run(name_or_path: str | Path) -> Run:
    """Read one run from a name under runs/ or a path to its directory."""
    directory = run_directory(name_or_path)
    run_json = json.loads((directory / "run.json").read_text())
    agents = list(run_json.get("agents") or [])
    episode_rows = read_jsonl(directory / "episodes.jsonl")
    judge_rows = read_jsonl(directory / "judge.jsonl")
    lone = lone_agent(agents, episode_rows)
    episodes = {agent_of(row["episode"], lone): row for row in episode_rows}
    judge = {agent_of(row["episode"], lone): row for row in judge_rows}
    eval_log = eval_log_path(run_json.get("eval_log"))
    timeline, board_reads = eval_log_data(directory, eval_log, run_json)
    task = run_json.get("task", "")
    domain = domain_of(task)
    return Run(
        name=run_name(directory),
        directory=directory,
        run_json=run_json,
        task=task,
        domain=domain,
        model=run_json.get("model", ""),
        agents=agents,
        roles=roles_of(agents, run_json, episodes),
        config=run_json.get("config") or {},
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


def run_names(runs_dir: Path = RUNS_DIR) -> list[str]:
    """Every run under runs/ with a run.json, nested ones included, skipping failed and cancelled runs."""
    names = []
    for run_json in sorted(runs_dir.rglob("run.json")):
        parts = run_json.parent.relative_to(runs_dir).parts
        if not parts or "tools" in parts or "trajectories" in parts:
            continue
        if any(part.startswith(SKIPPED_PREFIXES) for part in parts):
            continue
        names.append("/".join(parts))
    return names


def tools_dir(run: Run, tool: str) -> Path:
    """runs/<name>/tools/<tool>/, created when missing."""
    directory = run.directory / "tools" / tool
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def write_summary(run: Run, tool: str, payload: dict) -> Path:
    """Write runs/<name>/tools/<tool>/summary.json as {tool, run, written_at, **payload}."""
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
    path = run.directory / "tools" / tool / "summary.json"
    return json.loads(path.read_text()) if path.is_file() else None


def model_string(entry_or_string: str) -> str:
    """The Inspect model string of a registry entry; an Inspect string passes through."""
    return resolve(entry_or_string)


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
    """The run's directory: the path itself when it holds a run.json, else runs/<name>."""
    candidates = [Path(name_or_path), RUNS_DIR / str(name_or_path)]
    for candidate in candidates:
        if (candidate / "run.json").is_file():
            return candidate.resolve()
    raise FileNotFoundError(f"no run.json in {name_or_path} or {RUNS_DIR / str(name_or_path)}")


def run_name(directory: Path) -> str:
    """The run's path under runs/, or its directory name when it lives elsewhere."""
    try:
        return "/".join(directory.relative_to(RUNS_DIR.resolve()).parts)
    except ValueError:
        return directory.name


def read_jsonl(path: Path) -> list[dict]:
    """The rows of a JSON-lines file; empty when it is missing."""
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


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


def domain_of(task: str) -> str:
    """The domain of a run.json task name."""
    if task in DOMAINS:
        return DOMAINS[task]
    for prefix, domain in DOMAIN_PREFIXES:
        if task.startswith(prefix):
            return domain
    raise ValueError(f"unknown task {task!r}: expected one of {sorted(DOMAINS)}")


def roles_of(agents: list[str], run_json: dict, episodes: dict[str, dict]) -> dict[str, str]:
    """Each agent's hidden role: run.json swarm.roles, else the episode's role, else worker."""
    swarm_roles = (run_json.get("swarm") or {}).get("roles") or {}
    return {agent: swarm_roles.get(agent) or (episodes.get(agent) or {}).get("role") or "worker" for agent in agents}


def eval_log_path(value: str | None) -> Path | None:
    """The .eval file run.json names: the file itself, or the newest .eval in a named directory."""
    if not value:
        return None
    path = Path(value)
    if not path.is_absolute():
        path = ROOT / path
    if path.is_file():
        return path
    if path.is_dir():
        logs = sorted(path.glob("*.eval"), key=lambda log: log.stat().st_mtime)
        return logs[-1] if logs else None
    return None


def eval_log_data(directory: Path, eval_log: Path | None, run_json: dict) -> tuple[list[dict], dict[str, str]]:
    """The timeline and the board_read results, from the cache, else the eval log; run.json's timeline when empty."""
    cache = directory / "tools"
    timeline_cache, reads_cache = cache / "timeline.json", cache / "board_reads.json"
    cached = timeline_cache.is_file() and reads_cache.is_file()
    if eval_log is not None:
        logged = eval_log.stat().st_mtime
        fresh = cached and all(path.stat().st_mtime >= logged for path in (timeline_cache, reads_cache))
        if fresh:
            timeline, board_reads = json.loads(timeline_cache.read_text()), json.loads(reads_cache.read_text())
        else:
            timeline, board_reads = read_log(eval_log)
            cache.mkdir(parents=True, exist_ok=True)
            timeline_cache.write_text(json.dumps(timeline, indent=1) + "\n")
            reads_cache.write_text(json.dumps(board_reads, indent=1, ensure_ascii=False) + "\n")
    elif cached:
        timeline, board_reads = json.loads(timeline_cache.read_text()), json.loads(reads_cache.read_text())
    else:
        timeline, board_reads = [], {}
    return timeline or list((run_json.get("rules") or {}).get("timeline") or []), board_reads


def read_log(eval_log: Path) -> tuple[list[dict], dict[str, str]]:
    """TurnState:timeline from every sample's store, and every board_read result by tool call id.

    A call id that occurs twice in the log (mock models reuse "c1") is left out, so the transcript answers for it.
    """
    log = read_eval_log(str(eval_log))
    timeline, results, seen = [], {}, {}
    for sample in log.samples or []:
        timeline.extend(sample.store.get(TIMELINE_KEY) or [])
        for event in sample.events:
            if event.event == "tool" and event.function in BOARD_READ_TOOLS:
                seen[event.id] = seen.get(event.id, 0) + 1
                results[event.id] = message_text(event.result)
    return timeline, {call_id: text for call_id, text in results.items() if seen[call_id] == 1}


def read_board(directory: Path, timeline: list[dict]) -> list[Post]:
    """Every post on the board channel of every communication/<sample>.json, numbered by board position."""
    turns = {(entry.get("agent"), entry.get("turn")): entry.get("step") for entry in timeline}
    posts = []
    for path in sorted((directory / "communication").glob("*.json")):
        messages = json.loads(path.read_text()).get("messages") or []
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
        transcripts[agent_of(path.parent.name, lone)] = json.loads(path.read_text())
    return transcripts


def exploit_of(domain: str, run_json: dict) -> dict:
    """The domain's exploit vocabulary (instruction_levels.yaml vars), run.json access, and each agent's items."""
    variables = yaml.safe_load(INSTRUCTION_LEVELS.read_text())["vars"].get(domain, {})
    access = run_json.get("access") or {}
    holds = {agent: list(entry.get("holds") or []) for agent, entry in (access.get("agents") or {}).items()}
    return {**variables, "access": access, "holds": holds}
