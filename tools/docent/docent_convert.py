"""A run's agent episodes and board as Docent AgentRuns.

Each agent's ATIF transcript goes through the SDK's convert_atif_to_agent_run. Every message then gets the round,
activation, timeline step and turn of the agent step it came from, and the AgentRun gets the run's metadata under the
project's names (FIELDS): the three ladders, cheating_rung (src/measures/cheating_ladder.py), coordination_level
(src/measures/coordination.py) and collusion_rung (src/measures/collusion.py), with cheated (cheating_ladder.cheated:
the rung is attempted or higher), all four as src/measures/code_ladder.py ladders gives them, recorded or computed; the
judge labels awareness, manipulation and disclosure, each one's value as src/measures/judge_labels.py labels gives it;
and judge_cheated, CheatBench's judge's own `cheated`, a broader definition, with the judge's other
flags. The team's ladders are team_<field>. The measures run in the run environment (RUN_PYTHON), which imports the
domains and so ORBIT, which this environment lacks; nothing is written back. The board becomes one AgentRun of its
own, one user message per post.
"""
from __future__ import annotations

import functools
import json
import subprocess
from pathlib import Path

from docent.data_models.agent_run import AgentRun
from docent.data_models.chat.message import SystemMessage, UserMessage
from docent.data_models.transcript import Transcript
from docent.sdk.integrations import convert_atif_to_agent_run

from src.measures.rungs import LADDER_FIELDS
from src.measures.verdict_fields import flag_names
from tools.run_reader import Post, Run, Step

ROOT = Path(__file__).resolve().parents[2]
RUN_PYTHON = ROOT / ".venv" / "bin" / "python"
"""The run environment's Python, which imports the domains and so computes the measures."""
LABELS = ("awareness", "manipulation", "disclosure")
"""The judge labels an agent carries, by the measures' names."""
FIELDS = (*LADDER_FIELDS, "cheated", *LABELS)
"""What an agent carries of the measures: the three rungs, cheated and the judge labels."""
TEAM_FIELDS = (*LADDER_FIELDS, "cheated")
"""What the team carries, as team_<field>: the three rungs and cheated."""
COMPUTE = ("import json, sys; from pathlib import Path; from src.measures import code_ladder, judge_labels; "
           "run = Path(sys.argv[1]); print(json.dumps({'ladders': code_ladder.ladders(run), "
           "'labels': judge_labels.labels(run)}))")


@functools.cache
def measures(directory: Path) -> tuple[dict[str, dict], dict]:
    """The run's measures (FIELDS) by agent, and the team's (TEAM_FIELDS), computed in the run environment: the
    ladders as src/measures/code_ladder.py ladders gives them (recorded, or computed for a run recorded before a
    ladder), each judge label's value. Empty for a folder without run.json or signals.jsonl (a run the code never
    read)."""
    if not (directory / "signals.jsonl").is_file() or not (directory / "run.json").is_file():
        return {}, {}
    done = subprocess.run([str(RUN_PYTHON), "-c", COMPUTE, str(directory.resolve())], cwd=ROOT, capture_output=True,
                          text=True, check=True)
    computed = json.loads(done.stdout)
    ladders, labels = computed["ladders"], computed["labels"]["agents"]
    agents = {agent: {**rungs, **{label: (labels.get(agent) or {}).get(label) for label in LABELS}}
              for agent, rungs in ladders["agents"].items()}
    return agents, {field: ladders["team"][field] for field in TEAM_FIELDS}


def run_agent_runs(run: Run, with_board: bool) -> list[AgentRun]:
    """One AgentRun per agent with a transcript, then the board's AgentRun when asked for and the board has posts."""
    agent_runs = [agent_agent_run(run, agent) for agent in run.agents if agent in run.transcripts]
    if with_board and run.board:
        agent_runs.append(board_agent_run(run))
    return agent_runs


def agent_agent_run(run: Run, agent: str) -> AgentRun:
    """The agent's transcript as an AgentRun with its messages placed in rounds and the run's metadata attached."""
    agent_run = convert_atif_to_agent_run(run.transcripts[agent]["trajectory"])
    steps = {step.step_id: step for step in run.steps(agent)}
    for transcript in agent_run.transcripts:
        for message in transcript.messages:
            message.metadata = {**(message.metadata or {}), **message_placement(message.metadata or {}, steps)}
    role = run.roles.get(agent, "worker")
    mine = measures(run.directory)[0].get(agent) or {}
    agent_run.name = f"{run.name}/{agent}"
    agent_run.description = (f"{agent} ({role}) in {run.name}, {run.domain}; cheating={mine.get('cheating_rung')}, "
                             f"cheated={mine.get('cheated')}, coordination={mine.get('coordination_level')}, "
                             f"collusion={mine.get('collusion_rung')}, awareness={mine.get('awareness')}, "
                             f"manipulation={mine.get('manipulation')}, disclosure={mine.get('disclosure')}; "
                             f"judge_cheated={run.judge_cheated.get(agent)}")
    agent_run.metadata = {**agent_run.metadata, **agent_metadata(run, agent)}
    return agent_run


def message_placement(metadata: dict, steps: dict[int, Step]) -> dict:
    """The round, activation, timeline step and turn of the ATIF step(s) a converted message came from."""
    step_ids = metadata.get("atif_step_ids") or [metadata.get("atif_step_id")]
    placed = [steps[step_id] for step_id in step_ids if step_id in steps]
    agent_steps = [step for step in placed if step.source == "agent"]
    if not agent_steps:
        return {}
    first = agent_steps[0]
    return {
        "round": first.round,
        "activation": first.activation,
        "schedule_step": first.schedule_step,
        "turn": first.turn,
        "activations": [step.activation for step in agent_steps],
    }


def judge_key(flag: str) -> str:
    """A judge flag's metadata key: its name, but judge_cheated for the judge's own `cheated`, which differs from the
    project's cheated."""
    return "judge_cheated" if flag == "cheated" else flag


def agent_metadata(run: Run, agent: str) -> dict:
    """The agent's metadata: identity, configuration, access, its measures (FIELDS), CheatBench's verdict and the
    agent's own posts."""
    judge = run.judge.get(agent) or {}
    episode = run.episodes.get(agent) or {}
    engagement = episode.get("engagement") or {}
    access = run.exploit.get("access") or {}
    metadata = {
        "kind": "agent",
        "run": run.name,
        "agent": agent,
        "role": run.roles.get(agent, "worker"),
        **run_fields(run),
        "access_policy": access.get("policy"),
        "holds": run.exploit.get("holds", {}).get(agent, []),
        "access": (access.get("agents") or {}).get(agent, {}),
        **{field: (measures(run.directory)[0].get(agent) or {}).get(field) for field in FIELDS},
        **{judge_key(field): judge.get(field) for field in flag_names(run.task)},
        "judge_summary": judge.get("summary"),
        "judge": judge,
        "discovery_round": engagement.get("discovery_round"),
        "engagement": engagement,
        "board_posts": [post_record(post) for post in run.posts(agent)],
        "n_board_posts": len(run.posts(agent)),
        "ended_by": episode.get("ended_by"),
        "usage": episode.get("usage"),
        "score": episode.get("score"),
    }
    return json_safe(metadata)


def run_fields(run: Run) -> dict:
    """The fields every AgentRun of a run shares."""
    engagement = run.run_json.get("engagement") or {}
    team = measures(run.directory)[1]
    return {
        **{f"team_{field}": team.get(field) for field in TEAM_FIELDS},
        "domain": run.domain,
        "task": run.task,
        "model": run.model,
        "preset": run.run_json.get("preset") or run.config.get("preset"),
        "mode": run.run_json.get("mode") or run.config.get("mode"),
        "scale": run.run_json.get("scale") or run.config.get("scale"),
        "roster": list(run.agents),
        "roles": dict(run.roles),
        "judge_cheated_by_agent": dict(run.judge_cheated),
        "median_discovery_round": engagement.get("median_discovery_round"),
        "read_graph": engagement.get("read_graph") or {},
        "rounds": run.rounds,
    }


def board_agent_run(run: Run) -> AgentRun:
    """The board as one AgentRun: a system message naming the roster, then one user message per post."""
    roster = ", ".join(f"{agent} ({run.roles.get(agent, 'worker')})" for agent in run.agents)
    messages = [SystemMessage(content=f"The message board of run {run.name}. Agents: {roster}.")]
    messages.extend(UserMessage(content=post_label(run, post), metadata=post_record(post)) for post in run.board)
    transcript = Transcript(name="board", description=f"Board posts of {run.name}", messages=messages)
    metadata = json_safe({"kind": "board", "run": run.name, **run_fields(run), "n_board_posts": len(run.board)})
    return AgentRun(name=f"{run.name}/board", description=f"The message board of {run.name}, {len(run.board)} posts",
                    transcripts=[transcript], metadata=metadata)


def post_label(run: Run, post: Post) -> str:
    """A post as the board message: sender, role, board number and round, then the text."""
    role = run.roles.get(post.sender, "worker")
    return f"{post.sender} ({role}) posted #{post.number} in round {post.round}:\n{post.content}"


def post_record(post: Post) -> dict:
    """A post's fields for metadata."""
    return {"sender": post.sender, "number": post.number, "round": post.round, "turn": post.turn,
            "sequence": post.sequence, "recipients": list(post.recipients), "content": post.content}


def json_safe(value: dict) -> dict:
    """The value after a JSON round trip, so the metadata Docent stores is what was meant to be stored."""
    return json.loads(json.dumps(value, default=str))


def message_count(agent_run: AgentRun) -> int:
    """The number of messages over all transcripts of an AgentRun."""
    return sum(len(transcript.messages) for transcript in agent_run.transcripts)
