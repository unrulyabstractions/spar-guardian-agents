"""The files one sample writes into a run directory and its episode records.

sample_records writes each agent's transcript and prompt under trajectories/<episode>/, the sample's channel
messages under communication/ and its defense evidence under defense/, and returns one record per agent for
episodes.jsonl. Episode ids are "<sample>#<epoch>" for a single agent and "<sample>#<epoch>@<agent>" otherwise. A
record names its agent by the bare ID ("k7q2m"), its model as an Inspect string with the registry entry beside it
(model_key), and its transcript by a path from the run's folder, trajectories/<episode>/transcript.json.

    records = sample_records(view, run_dir)
    episode = episode_id(view, "k7q2m")
"""
from __future__ import annotations

import json
from pathlib import Path

from src.export._export_run_blocks import (
    cheatbench,
    ended_by,
    engagement_record,
    ledger_record,
    notice_counts,
    own_ending,
    probes_block,
    recorded_episode_facts,
    rules_facts,
    rules_state,
    team_submission,
    validity_record,
)
from src.export.atif_transcripts import episode_model, episode_transcript, model_key
from src.export.eval_sample_view import (
    LIMIT_FAILURES,
    SampleView,
    agent_events,
    agent_messages,
    agent_version,
    defense_counts,
    defense_evidence,
    error_and_failure,
    roster,
    topology,
)


# --------------------------------------------------------------------------
# files per sample
# --------------------------------------------------------------------------
def sample_key(view: SampleView) -> str:
    """The sample's key "<id>#<epoch>": a lone agent's episode id and the name of the sample's files."""
    return f"{view.id}#{view.epoch}"


def episode_id(view: SampleView, agent: str) -> str:
    """The agent's episode: the sample's key for a lone agent, "<sample>#<epoch>@<agent>" in a team."""
    return sample_key(view) if len(roster(view)) == 1 else f"{sample_key(view)}@{agent}"


def _write_communication(view: SampleView, run_dir: Path) -> None:
    keys = ("CommunicationState:messages", "CommunicationState:events")
    comm = {k.split(":", 1)[1]: view.store_get(k) for k in keys if view.store_get(k)}
    if not comm:
        return
    comm_dir = run_dir / "communication"
    comm_dir.mkdir(exist_ok=True)
    (comm_dir / f"{sample_key(view)}.json").write_text(json.dumps(comm, indent=1, default=str))


def _write_defense(view: SampleView, run_dir: Path, defenses_cfg: list, evidence: dict) -> None:
    if not (defenses_cfg or evidence["interventions"]):
        return
    def_dir = run_dir / "defense"
    def_dir.mkdir(exist_ok=True)
    payload = {"defenses": defenses_cfg, **evidence}
    (def_dir / f"{sample_key(view)}.json").write_text(json.dumps(payload, indent=1, default=str))


# --------------------------------------------------------------------------
# one episode
# --------------------------------------------------------------------------
def _base_record(view: SampleView, agent: str, defenses_cfg: list, evidence: dict) -> dict:
    cb, model = cheatbench(view), episode_model(view, agent)
    scores = {k: (v.value if hasattr(v, "value") else v) for k, v in (view.scores or {}).items()}
    return {
        "id": view.id, "episode": episode_id(view, agent), "replicate": view.epoch,
        "task": cb.get("task"), "model": model, "model_key": model_key(model), "agent": agent,
        "orbit_roster": roster(view), "topology": topology(view), "role": (cb.get("roles") or {}).get(agent),
        "defenses": [d["name"] for d in defenses_cfg],
        "defense": defense_counts(evidence, agent),
        "agent_version": agent_version(),
        "prompt_file": cb.get("prompt_file"),
        "wall_time": view.total_time,
        "limit": view.limit.type if view.limit else None,
        "score": scores,
    }


def _write_episode(view: SampleView, run_dir: Path, agent: str, error, failure, rules: dict | None,
                   submission: dict | None) -> tuple[dict, list[dict]]:
    """Write one agent's transcript and prompt; return the record fields that depend on them, and its ATIF steps."""
    transcript, info = episode_transcript(view, agent)
    ep_dir = run_dir / "trajectories" / episode_id(view, agent)
    ep_dir.mkdir(parents=True, exist_ok=True)
    transcript_path = ep_dir / "transcript.json"
    transcript_path.write_text(json.dumps(transcript, indent=1))
    (ep_dir / "prompt.txt").write_text(transcript["prompt"])
    # A limit ends the sample, not every agent in it: an agent whose own act or a turn rule ended its episode
    # (TurnState.ended) finished before the limit hit the others.
    if own_ending(view, rules, agent) is not None and failure in LIMIT_FAILURES:
        ep_error, ep_failure = None, None
    else:
        ep_error, ep_failure = error, failure
    fields = {
        "final_text": info["final_text"],
        "n_turns": info["n_agent_steps"], "n_tool_calls": info["n_tool_calls"],
        "usage": info["usage"], "transcript_path": transcript_path.relative_to(run_dir).as_posix(),
        "error": ep_error, "failure": ep_failure, "ok": ep_error is None,
        "ended_by": ended_by(view, agent, rules, submission, ep_failure),
    }
    return fields, transcript["trajectory"].get("steps", [])


def _no_transcript(error, failure) -> dict:
    """An agent that never produced a projected conversation: a failed episode the judge skips."""
    return {"final_text": None, "n_turns": 0, "n_tool_calls": 0, "usage": None, "transcript_path": None,
            "error": error or "no agent conversation was projected into the sample",
            "failure": failure or "harness", "ok": False, "ended_by": "error"}


def sample_records(view: SampleView, run_dir: Path) -> list[dict]:
    """Write one sample's files and return its episode records."""
    agents = roster(view)
    if not agents:
        raise ValueError(f"sample {view.id}: no ORBIT roster in metadata['experiment']")
    meta = view.metadata or {}
    error, failure = error_and_failure(view)
    _write_communication(view, run_dir)
    defenses_cfg = meta.get("experiment", {}).get("defenses") or []
    evidence = defense_evidence(view)
    _write_defense(view, run_dir, defenses_cfg, evidence)
    rules = rules_state(view)
    probes = probes_block(view) or {}
    submission = team_submission(view)
    notices = notice_counts(view)
    records = []
    for agent in agents:
        record = _base_record(view, agent, defenses_cfg, evidence)
        steps = []
        if agent_messages(view, agent):
            fields, steps = _write_episode(view, run_dir, agent, error, failure, rules, submission)
            record.update(fields)
        else:
            record.update(_no_transcript(error, failure))
        record["rules"] = rules_facts(rules, agent)
        record["probes"] = probes.get(agent)
        record["engagement"] = engagement_record(view, agent, rules, submission, notices)
        model_events = agent_events(view, agent, "model")
        record["validity"] = validity_record(steps, len(model_events), record["engagement"],
                                             len(agents) > 1)
        record["ledger"] = ledger_record(record["model"], model_events, record.get("usage"))
        record.update(recorded_episode_facts(view, agent))
        records.append(record)
    return records
