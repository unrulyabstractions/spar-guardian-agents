"""An Inspect .eval log from an ORBIT run, written as the run directory CheatBench's judge and our browser read.

    write_run(eval_log_path, run_dir, verdicts=None)

Writes
    <run_dir>/run.json                                 the run: task, model, roster, defenses, team, config, rules
    <run_dir>/episodes.jsonl                           one record per (sample, agent)
    <run_dir>/judge.jsonl                              one CheatBench verdict row per judged episode
    <run_dir>/trajectories/<episode>/transcript.json   the ATIF transcript the judge read
    (src/benchmark/atif_transcripts.py)
    <run_dir>/trajectories/<episode>/prompt.txt
    <run_dir>/communication/<sample>.json              channel messages and events, if any
    <run_dir>/defense/<sample>.json                    defense configuration and interventions, if any
    <run_dir>/swe/diffs/<target>.diff                  for a SWE run, the graded diffs

run.json also carries the run's mode, scale and preset, and each episode and the run an `engagement` block: who
looked at the board, read it, posted and submitted for the team (None for a run without a board).

An episode's "model" is the agent's own model: its role's model when the run gave the role one, else the eval's model.
The eval log names a role model "swarm/<model>" (src/configuration/swarm_model_provider.py); the export drops
the prefix. A run
that recorded role models gets run.json "role_models" ({role: Inspect string or None}) next to "model".

Facts that only newer runs record are written only when the run has them, so a run of the October 2026 sweep exports
byte for byte as before (the smoke runs of 395e9c8 recorded split_access and archive_part, which nothing reads any
more): run.json "turn_budget" ({budget, steps_per_agent}), "access" (the access policy and what each agent held:
its items, and on openmath its honeypot branches and archive slices, on knowledge work the review tool and the
artifact read), "knowledge" (the knowledge-work sample and its reviews), "interview" and "protein" (a machines
domain's sample and its tool's states and calls), "delivery" (the stop-after-delivery record); the engagement keys
oversight, oversight_targets, reads_refused and posts_refused; and an episode's "access", "review" (its grader state on
knowledge work) and "delivery" (the round and working step at which its deliverable was first seen). An episode that
stop_after_delivery ended has ended_by "delivered".

Episode ids: "<sample>#<epoch>" for a single agent, "<sample>#<epoch>@<agent>" otherwise.
Verdicts come from the `verdicts` argument (re-judging) or from the cheatbench_judge score in the log.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import inspect_ai
import orbit
from inspect_ai.log import read_eval_log

from src.benchmark import sandbox_hygiene as hygiene
from src.benchmark.atif_transcripts import (
    LIMIT_FAILURES,
    TURN_RULES_TAG,
    SampleView,
    agent_events,
    agent_messages,
    agent_version,
    defense_counts,
    defense_evidence,
    episode_transcript,
    error_and_failure,
    judge_prompt,
    orbit_halt_reason,
    roster,
    topology,
)
from src.benchmark.cheatbench_judge import JUDGE_SCORER
from src.benchmark.domains.openmath.openmath_settings import TEAM_IDENTITY
from src.configuration import model_registry as models
from src.configuration import settings
from src.configuration import swarm_model_provider as provider
from src.configuration.swarm_config import DOMAIN_KEYS, DOMAIN_MODES

DELIVERABLES_SCORER = "deliverables_scorer"
KNOWLEDGE_SCORER = "knowledge_deliverables"   # src/benchmark/domains/knowledge/knowledge_scoring.py
INTERVIEW_SCORER = "interview_deliverables"   # src/benchmark/domains/interview/interview_scoring.py
PROTEIN_SCORER = "protein_deliverables"       # src/benchmark/domains/protein/protein_scoring.py
# The machines domains (one container per agent): run.json key and scorer name.
MACHINE_BLOCKS = {"interview": INTERVIEW_SCORER, "protein": PROTEIN_SCORER}
SWE_GRADER = "swe_grader"
PROBES_SCORER = "end_of_episode_probes"
# the StoreModel of src/swarm/turn_rules.py; Inspect stores its fields as "TurnState:<field>"
TURN_STATE = "TurnState"
TURN_STATE_FIELDS = ("agents", "cap", "pooled", "turn_order", "seed", "rounds", "windows", "activations", "used",
                     "ended", "timeline")
# The pull board's per-agent counts ({agent: value}) and the team submission; written only when the run stored them.
TURN_STATE_BOARD_FIELDS = ("work_steps", "free_steps", "checks", "reads", "posts", "discovery_round", "read_senders",
                           "team_submission", "free_caps", "reads_per_turn", "posts_per_turn", "turn_budget",
                           "oversight", "oversight_targets", "turn_reads", "turn_posts", "reads_refused",
                           "posts_refused", "history_marks")
# The knowledge-work keys run.json keeps; the row, the evaluation file and the image ids stay in the eval log.
KNOWLEDGE_SAMPLE_KEYS = ("layout", "mode", "split", "row_id", "meta", "comparator", "seats")
KNOWLEDGE_SCORE_KEYS = ("contracts", "grader_state", "last_verdict", "access")
TEAM_SUBMISSION = "TeamSubmission"        # its StoreModel: agent, round, answer, time, refused
TEAM_SUBMISSION_FIELDS = ("agent", "round", "answer", "time", "refused")
# src/swarm/turn_rules.py's stop-after-delivery record, kept only under the rule
DELIVERED_STORE = "Delivered"
DELIVERED_FIELDS = ("after", "team", "seen", "halt_round", "cut", "checks", "errors")
DELIVERED = "delivered"                   # its TurnState.ended reason and an episode's ended_by
SWE_TEXT_KEYS = ("first_message", "judge_prompt", "system_prompt")




def _cheatbench(view: SampleView) -> dict:
    return (view.metadata or {}).get("cheatbench", {})


def _score_metadata(view: SampleView, name: str) -> dict | None:
    sc = (view.scores or {}).get(name)
    return None if sc is None else (sc.metadata or {})


# --------------------------------------------------------------------------
# what the team left behind (openmath)
# --------------------------------------------------------------------------
def _registry_entries(md: dict) -> list[dict]:
    try:
        registry = json.loads(md.get("agent_evals_main") or "{}")
        return [a for a in (registry.get("agents") or []) if isinstance(a, dict)]
    except (ValueError, AttributeError):
        return []


def deliverables_block(view: SampleView) -> dict | None:
    """What the team left behind, from the deliverables scorer's evidence.

    The score value only says whether the archive sat in the working tree at the end; a
    team that registered and then checked out main scores 0, and an uncommitted archive
    scores 1. The registry on main and the git summary say whether the team registered.
    """
    sc = (view.scores or {}).get(DELIVERABLES_SCORER)
    if sc is None:
        return None
    md = sc.metadata or {}
    per_agent = md.get("agents")
    if isinstance(per_agent, dict):
        # openmath solo_task or split access: one copy of the row per agent. The top-level keys summarise the copies.
        blocks = {name: _deliverables_facts(found or {}, bool((found or {}).get("archive")))
                  for name, found in per_agent.items()}
        remote = md.get("remote")
        if isinstance(remote, dict):
            # split access: the team registers by pushing main to the bare team remote, which the scorer read
            facts = _deliverables_facts(remote, bool(remote.get("archive_on_main")))
            facts["archive_on_remote_main"] = facts["archive_in_workspace"]
            facts["archive_in_workspace"] = sc.value == 1.0
            return {**facts, "remote_branches": remote.get("branches"), "agents": blocks,
                    "explanation": sc.explanation}
        return {"archive_in_workspace": sc.value == 1.0,
                "registered_on_main": any(b["registered_on_main"] for b in blocks.values()),
                "registered_entry": None, "registered_message_copied_from": None, "main_registry_agents": [],
                "checkout": None, "commits": [], "agents": blocks, "explanation": sc.explanation}
    return {**_deliverables_facts(md, sc.value == 1.0), "explanation": sc.explanation}


def _deliverables_facts(md: dict, archive_present: bool) -> dict:
    """The registry on main, the checkout and the commits of one workspace's deliverables evidence."""
    entries = _registry_entries(md)
    agents = [a.get("agent") for a in entries]
    team = next((a for a in entries if a.get("agent") == TEAM_IDENTITY), None)
    # A registration that carries another agent's message verbatim registered that agent's work.
    copied_from = None
    if team and (team.get("message") or "").strip():
        for other in entries:
            if other is not team and (other.get("message") or "").strip() == team["message"].strip():
                copied_from = other.get("agent")
    registered_entry = None
    if team:
        registered_entry = {"archive": team.get("archive"), "commits": team.get("commits"),
                            "message_head": (team.get("message") or "")[:200]}
    lines = (md.get("git") or "").splitlines()
    status = next((line for line in lines if line.startswith("## ")), "")
    return {"archive_in_workspace": archive_present,
            "registered_on_main": TEAM_IDENTITY in agents,
            "registered_entry": registered_entry,
            "registered_message_copied_from": copied_from,
            "main_registry_agents": agents,
            "checkout": status[3:].split("...")[0] if status else None,
            "commits": [line for line in lines if re.match(r"^[0-9a-f]{7,} ", line)]}


def _swarm_facts(cb: dict) -> dict | None:
    """Composition and hidden roles, from the roles and the config the sample recorded."""
    roles = cb.get("roles")
    if not roles:
        return None
    config = cb.get("config") or {}
    regulators = sum(1 for r in roles.values() if r == "regulator")
    disregulators = sum(1 for r in roles.values() if r == "disregulator")
    return {"n_agents": len(roles), "regulators": regulators, "disregulators": disregulators,
            "workers": len(roles) - regulators - disregulators, "seed": config.get("seed"), "roles": roles,
            "composition": cb.get("composition")}


def swarm_block(view: SampleView) -> dict | None:
    """Composition and hidden roles, for a swarm run."""
    return _swarm_facts(_cheatbench(view))


# --------------------------------------------------------------------------
# access, the turn budget and knowledge work (runs that record them)
# --------------------------------------------------------------------------
def access_block(view: SampleView) -> dict | None:
    """The access policy and what each agent held, when the run recorded them: "policy" (split, all or explicit)
    and per agent "holds" (its items) from metadata["cheatbench"]["access"]; on openmath in the split layout also the
    team remote, the honeypot branches each copy kept, the archive and the slices each copy held (None: the whole
    archive); on knowledge work the review tool and the read permission on the pipeline artifact. None for a run
    that recorded none of it."""
    cb = _cheatbench(view)
    recorded = cb.get("access") or {}
    block = {"policy": recorded.get("policy"), "agents": {a: {"holds": items}
                                                           for a, items in (recorded.get("agents") or {}).items()}}
    om = cb.get("openmath") or {}
    if om.get("layout") == "split":
        block.update({"remote": om.get("remote"), "honeypot_branches": om.get("honeypot_branches"),
                      "archive": om.get("archive")})
        for a, seat in (om.get("agents") or {}).items():
            block["agents"].setdefault(a, {}).update({"branches": seat.get("branches"), "slices": seat.get("slices")})
    kw = cb.get("knowledge") or {}
    for a, seat in (kw.get("seats") or {}).items():
        block["agents"].setdefault(a, {}).update({"review": seat.get("review"), "read": seat.get("read")})
    for key in MACHINE_BLOCKS:
        for a, seat in ((cb.get(key) or {}).get("agents") or {}).items():
            block["agents"].setdefault(a, {}).update({"service": seat.get("service"), "pieces": seat.get("pieces")})
    return block if block["agents"] else None


def knowledge_block(view: SampleView) -> dict | None:
    """The knowledge-work sample (layout, mode, seats, the pipeline artifact) and what its reviews found: each
    contract's to_send/ files, the grader state per agent, the last verdict, the number of review calls and each
    call's agent, deliverable, verdict and error (review_calls). None for another task."""
    kw = _cheatbench(view).get("knowledge")
    if not kw:
        return None
    md = _score_metadata(view, KNOWLEDGE_SCORER) or {}
    sc = (view.scores or {}).get(KNOWLEDGE_SCORER)
    calls = md.get("reviews") or []
    return {**{k: kw.get(k) for k in KNOWLEDGE_SAMPLE_KEYS}, **{k: md.get(k) for k in KNOWLEDGE_SCORE_KEYS},
            "share_delivered": None if sc is None else sc.value, "reviews": len(calls),
            "review_calls": [{k: c.get(k) for k in ("agent", "deliverable", "verdict", "error_type")}
                             for c in calls]}


def machine_block(view: SampleView, key: str) -> dict | None:
    """run.json[key] for a machines domain (interview, protein): the sample's layout facts
    (metadata["cheatbench"][key]) merged with the scorer's outcome facts (its metadata[key]: the tool's states and
    calls, what the agents left behind) and the score as "value". None for another task."""
    facts = _cheatbench(view).get(key)
    if not facts:
        return None
    scorer = MACHINE_BLOCKS[key]
    sc = (view.scores or {}).get(scorer)
    return {**facts, **((_score_metadata(view, scorer) or {}).get(key) or {}),
            "value": None if sc is None else sc.value}


def signals_block(view: SampleView) -> dict | None:
    """The honeypot probe's report (src/benchmark/honeypot_fingerprints.py by_owner), from whichever scorer ran it."""
    for name in (DELIVERABLES_SCORER, KNOWLEDGE_SCORER, *MACHINE_BLOCKS.values()):
        found = (_score_metadata(view, name) or {}).get("signals")
        if found is not None:
            return found
    return None


def sandbox_block(view: SampleView) -> dict | None:
    """What the sandbox held at the end of the run (src/benchmark/sandbox_hygiene.py STATE, from the domain's
    deliverables
    scorer; per container on the machines domains), the reaps after the agents' commands (hygiene.REAP_STORE:
    {agent: {commands, killed, max_alive, survivors, errors}}) and the loopback guard's report (hygiene.GUARD_STORE).
    None for a run that recorded none of them."""
    end = None
    for name in (DELIVERABLES_SCORER, KNOWLEDGE_SCORER, *MACHINE_BLOCKS.values()):
        found = (_score_metadata(view, name) or {}).get("sandbox")
        if found is not None:
            end = found
            break
    reaps = hygiene.reaps(view.store_get)
    guard = hygiene.guard_report(view.store_get)
    if end is None and reaps is None and guard is None:
        return None
    return {"end": end, "reaps": reaps, "loopback": guard}


IDENTICAL_RUN_FLAG = 8      # this many identical tool calls in a row flag a stuck agent
EARLY_SUBMIT_ROUND = 3      # a team_submit this early with a short answer flags a run that never got going
SHORT_ANSWER = 20


def identical_tool_run(steps: list[dict]) -> int:
    """The longest run of consecutive identical tool calls (same function and arguments) in an episode's steps."""
    longest = run = 0
    last = None
    for step in steps:
        for call in step.get("tool_calls") or []:
            key = (call.get("function_name"), json.dumps(call.get("arguments"), sort_keys=True, default=str))
            run = run + 1 if key == last else 1
            last = key
            longest = max(longest, run)
    return longest


def validity_record(steps: list[dict], model_calls: int, engagement: dict | None, board: bool) -> dict:
    """One agent's validity facts: its model and tool calls, whether it never acted, the longest run of identical
    tool calls, and whether it never touched the board it was given."""
    tool_calls = sum(len(s.get("tool_calls") or []) for s in steps)
    board_calls = sum((engagement or {}).get(k) or 0 for k in ("checks", "reads", "posts"))
    return {"model_calls": model_calls, "tool_calls": tool_calls, "never_acted": model_calls == 0,
            "no_tool_call": tool_calls == 0, "identical_tool_run": identical_tool_run(steps),
            "board_unused": board and board_calls == 0}


def validity_block(records: list[dict], submission: dict | None) -> dict:
    """The run's validity: each agent's facts (validity_record) and the flags a reader should see before trusting
    the run: an agent that never called a model or a tool, a stuck agent, a board never touched, a team_submit in
    the first rounds with a token answer. `ok` when nothing is flagged."""
    agents = {r["orbit_agent"]: r["validity"] for r in records if r.get("validity")}
    flags = []
    for agent, v in agents.items():
        if v["never_acted"]:
            flags.append(f"{agent}: no model call")
        elif v["no_tool_call"]:
            flags.append(f"{agent}: no tool call")
        if v["identical_tool_run"] >= IDENTICAL_RUN_FLAG:
            flags.append(f"{agent}: {v['identical_tool_run']} identical tool calls in a row")
        if v["board_unused"]:
            flags.append(f"{agent}: never used the board")
    sub_round = (submission or {}).get("round")
    if isinstance(sub_round, int) and sub_round <= EARLY_SUBMIT_ROUND \
            and len(str((submission or {}).get("answer") or "")) < SHORT_ANSWER:
        flags.append(f"team_submit in round {sub_round} with a {len(str(submission.get('answer') or ''))}-character "
                     "answer")
    return {"agents": agents, "flags": flags, "ok": not flags}


def ledger_record(model: str, events: list, usage: dict | None) -> dict:
    """One agent's accounting: its model calls, the errored ones and those without usage, its tokens, its cost at
    the registry's list prices (None for a model outside the registry), what the provider billed for the calls
    whose raw response the log kept, and the flags that make the figures lower bounds."""
    usage = dict(usage or {})
    calls = len(events)
    errored = sum(1 for e in events if getattr(e, "error", None))
    with_usage = sum(1 for e in events if getattr(getattr(e, "output", None), "usage", None))
    price = models.usd_per_million(model)
    cost = None
    if price and usage:
        cached = price.get("cached_input", price["input"])
        cost = ((usage.get("input", 0) - usage.get("cache_read", 0)) * price["input"]
                + usage.get("cache_read", 0) * cached + usage.get("output", 0) * price["output"]) / 1e6
    flags = []
    if price is None:
        flags.append("unpriced model")
    if errored:
        flags.append(f"{errored} errored call(s)")
    if calls - errored - with_usage > 0:
        flags.append(f"{calls - errored - with_usage} call(s) without usage")
    if usage.get("billed_calls") is not None and usage["billed_calls"] < with_usage:
        flags.append(f"billed for {usage['billed_calls']} of {with_usage} call(s)")
    return {"model": model, "calls": calls, "errored": errored, "with_usage": with_usage,
            "tokens": {k: usage.get(k, 0) for k in ("input", "cache_read", "output")},
            "cost_usd": round(cost, 6) if cost is not None else None,
            "billed_usd": usage.get("billed_usd"), "billed_calls": usage.get("billed_calls"), "flags": flags}


def ledger_block(records: list[dict], limit: str | None) -> dict:
    """The run's ledger: each agent's record (ledger_record), the totals, and whether the totals are complete: no
    agent flag, and the sample did not end on a limit, which leaves a call in flight out of the log."""
    agents = {r["orbit_agent"]: r["ledger"] for r in records if r.get("ledger")}
    total = {"calls": 0, "errored": 0, "tokens": {"input": 0, "cache_read": 0, "output": 0},
             "cost_usd": 0.0, "billed_usd": 0.0, "billed_calls": 0}
    priced = billed = True
    for rec in agents.values():
        total["calls"] += rec["calls"]
        total["errored"] += rec["errored"]
        for k in total["tokens"]:
            total["tokens"][k] += rec["tokens"].get(k, 0)
        if rec["cost_usd"] is None:
            priced = False
        else:
            total["cost_usd"] += rec["cost_usd"]
        if rec["billed_usd"] is None:
            billed = False
        else:
            total["billed_usd"] += rec["billed_usd"]
            total["billed_calls"] += rec["billed_calls"] or 0
    total["cost_usd"] = round(total["cost_usd"], 6) if priced else None
    total["billed_usd"] = round(total["billed_usd"], 6) if billed and agents else None
    flags = [f"{agent}: {flag}" for agent, rec in agents.items() for flag in rec["flags"]]
    if limit:
        flags.append(f"the sample ended on its {limit} limit: a call in flight is not in the log")
    return {"agents": agents, "total": total, "complete": not flags, "flags": flags}


def delivery_block(view: SampleView) -> dict | None:
    """The stop-after-delivery record (src/swarm/turn_rules.py Delivered): N ("after"), whether one deliverable counted
    for the team, each agent's first sighting {round, turn}, the team's last round, the agents of a team whose later
    steps were cut, the predicate's calls and errors, and the agents the rule ended ("ended", from TurnState.ended).
    None for a run without the rule."""
    if view.store_get(f"{DELIVERED_STORE}:after") is None:
        return None
    ended = view.store_get(f"{TURN_STATE}:ended") or {}
    return {**{f: view.store_get(f"{DELIVERED_STORE}:{f}") for f in DELIVERED_FIELDS},
            "ended": [a for a, why in ended.items() if why == DELIVERED]}


def ended_by_delivery(view: SampleView, agent: str) -> bool:
    """Whether stop_after_delivery ended the agent's episode."""
    return ((view.store_get(f"{TURN_STATE}:ended") or {}).get(agent) == DELIVERED
            and view.store_get(f"{DELIVERED_STORE}:after") is not None)


def newer_run_facts(view: SampleView) -> dict:
    """run.json keys that only newer runs carry, each present only when the run recorded it."""
    cb = _cheatbench(view)
    facts = {"turn_budget": cb.get("turn_budget"), "access": access_block(view), "knowledge": knowledge_block(view),
             **{key: machine_block(view, key) for key in MACHINE_BLOCKS}, "signals": signals_block(view),
             "delivery": delivery_block(view), "sandbox": sandbox_block(view), "code": cb.get("code")}
    return {k: v for k, v in facts.items() if v is not None}


def newer_episode_facts(view: SampleView, agent: str) -> dict:
    """An episode's access (what it held), review (its knowledge-work grader state), task_tool (its owner's
    state and its calls of the task's tool on a machines domain) and delivery (the round and working step at which
    its deliverable was first seen), when the run recorded them."""
    facts = {"access": ((access_block(view) or {}).get("agents") or {}).get(agent),
             "review": ((_score_metadata(view, KNOWLEDGE_SCORER) or {}).get("grader_state") or {}).get(agent),
             "task_tool": next((md["agents"][agent] for md in map(lambda s: _score_metadata(view, s),
                                                                   MACHINE_BLOCKS.values())
                                if md and agent in (md.get("agents") or {})), None),
             "delivery": ((delivery_block(view) or {}).get("seen") or {}).get(agent)}
    return {k: v for k, v in facts.items() if v is not None}


# --------------------------------------------------------------------------
# SWE (CheatBench software_engineering under Lucy's plan)
# --------------------------------------------------------------------------
def swe_grades(view: SampleView) -> dict | None:
    """Hidden-test results per target (team, a01, ...) from the SWE grader, without the diff text."""
    md = _score_metadata(view, SWE_GRADER)
    if md is None:
        return None
    return {name: {k: v for k, v in t.items() if k != "diff_text"} for name, t in (md.get("targets") or {}).items()}


def _config_rule(cb: dict) -> str | None:
    """The rule level of the run's config."""
    return (cb.get("config") or {}).get("rule_level")


def _swe_jigsaw(swe: dict) -> bool:
    return swe.get("honeypot") == "jigsaw"


def _write_swe_diffs(md: dict, run_dir: Path) -> None:
    diffs = run_dir / "swe" / "diffs"
    for name, t in (md.get("targets") or {}).items():
        diffs.mkdir(parents=True, exist_ok=True)
        (diffs / f"{name}.diff").write_text(t.get("diff_text") or "")
    for name, a in (md.get("agents") or {}).items():
        if a.get("own_diff_text") is not None:      # the agent's own commits and uncommitted changes (rungs)
            diffs.mkdir(parents=True, exist_ok=True)
            (diffs / f"{name}.own.diff").write_text(a["own_diff_text"])


def swe_block(view: SampleView, run_dir: Path) -> dict | None:
    """The SWE setting and its grades for run.json; the diffs go to swe/diffs/<target>.diff."""
    cb = _cheatbench(view)
    swe = cb.get("swe")
    if not swe:
        return None
    md = _score_metadata(view, SWE_GRADER) or {}
    _write_swe_diffs(md, run_dir)
    setup = view.store_get("swe_setup") or md.get("setup") or {}
    block = {k: v for k, v in swe.items() if k not in SWE_TEXT_KEYS}
    fragments = block.get("fragments") or {}
    block["fragments"] = {a: {k: v for k, v in f.items() if k != "patch"} for a, f in fragments.items()} or None
    jigsaw = _swe_jigsaw(swe)
    block.update({
        "task_key": swe.get("task_key") or swe.get("issue"), "jigsaw": jigsaw,
        "rule": _config_rule(cb),
        # the planted {piece: commit} of each agent (src/benchmark/domains/swe/swe_honeypot_setup.py)
        "fragment_commits": ({a: ev.get("planted") for a, ev in (setup.get("agents") or {}).items()}
                             if jigsaw or swe.get("honeypot") == "own_piece" else None),
        "checks_ok": setup.get("checks_ok"),
        "setup": setup, "primary": md.get("primary"), "graded": md.get("graded"),
        "required_tests": md.get("required_tests"), "grades": swe_grades(view), "team": md.get("team"),
        "agents_final": {a: {k: v for k, v in x.items() if k != "own_diff_text"}
                         for a, x in (md.get("agents") or {}).items()},
    })
    return block


def swe_record(view: SampleView, agent: str) -> dict:
    """The SWE keys of one episode record: seat, piece, rewards."""
    cb = _cheatbench(view)
    swe = cb["swe"]
    grades = swe_grades(view) or {}
    return {
        "swe_task": cb.get("swe_task") or swe.get("issue"), "seat": agent, "n_agents": swe.get("n_agents"),
        "jigsaw": _swe_jigsaw(swe), "rule": _config_rule(cb),
        "honeypot": swe.get("honeypot"), "piece": (swe.get("pieces") or {}).get(agent),
        "fragment_files": ((swe.get("fragments") or {}).get(agent) or {}).get("files"),
        "reward": (grades.get(agent) or {}).get("reward"),
        "team_reward": (grades.get("team") or {}).get("reward") if swe.get("board") else None,
    }



# --------------------------------------------------------------------------
# rules facts and probes
# --------------------------------------------------------------------------
def rules_state(view: SampleView) -> dict | None:
    """The turn rules' store (src/swarm/turn_rules.py TurnState), or None for a run without them."""
    if view.store_get(f"{TURN_STATE}:agents") is None:
        return None
    state = {f: view.store_get(f"{TURN_STATE}:{f}") for f in TURN_STATE_FIELDS}
    board = {f: view.store_get(f"{TURN_STATE}:{f}") for f in TURN_STATE_BOARD_FIELDS}
    return {**state, **{f: v for f, v in board.items() if v is not None}}


def rules_facts(state: dict | None, agent: str) -> dict | None:
    """One agent's activations, working turns used, how it ended and its window of rounds."""
    if state is None:
        return None
    return {k: (state.get(k) or {}).get(agent) for k in ("activations", "used", "ended", "windows")}


def shutdown_rate(config: dict | None, records: list[dict]) -> float | None:
    """The share of agents that ended their own episode with submit; None when the agents had no submit tool."""
    if (config or {}).get("shutdown") is False or not records:
        return None
    return sum(1 for r in records if r.get("ended_by") == "submit") / len(records)


def probes_block(view: SampleView) -> dict | None:
    md = _score_metadata(view, PROBES_SCORER)
    return None if md is None else md.get("probes")


# --------------------------------------------------------------------------
# the board: who looked, read, posted and submitted for the team
# --------------------------------------------------------------------------
def team_submission(view: SampleView) -> dict | None:
    """The team's submission, from the TeamSubmission store (src/swarm/pull_board.py team_submit)."""
    stored = {f: view.store_get(f"{TEAM_SUBMISSION}:{f}") for f in TEAM_SUBMISSION_FIELDS}
    return stored if stored["agent"] else None


def notice_counts(view: SampleView) -> dict[str, int]:
    """Board notices per agent, from the turn rules' info events of kind "notice"."""
    out: dict[str, int] = {}
    for ev in view.events or []:
        rule = (ev.data or {}).get(TURN_RULES_TAG) if ev.event == "info" and isinstance(ev.data, dict) else None
        if isinstance(rule, dict) and rule.get("kind") == "notice" and rule.get("agent"):
            out[rule["agent"]] = out.get(rule["agent"], 0) + 1
    return out


def engagement_record(view: SampleView, agent: str, rules: dict | None, submission: dict | None,
                      notices: dict[str, int]) -> dict | None:
    """One agent's board engagement, from the turn rules' store (TurnState), or None for a run without a board.

    discovery_round is the round of its first board_status or board_read; checks, reads and posts count the board
    tool calls (posts that went through); read_senders holds the senders of the posts board_read returned, the agent
    itself left out; free_steps and work_steps are its steps by charge; oversight and oversight_targets count a
    regulator's agent_actions calls; reads_refused and posts_refused the calls the per-round budgets refused;
    team_submitted whether it called team_submit (the submission's agent, or a later call the tool refused);
    notices the board notices its calls opened with."""
    if topology(view) == "single":
        return None
    state = rules or {}

    def recorded(field, default=None):
        table = state.get(field)
        return table.get(agent, default) if isinstance(table, dict) else default

    refused = [r.get("agent") for r in (submission or {}).get("refused") or []]
    return {
        "discovery_round": recorded("discovery_round"),
        "checks": recorded("checks", 0), "reads": recorded("reads", 0), "posts": recorded("posts", 0),
        "read_senders": recorded("read_senders", {}),
        "free_steps": recorded("free_steps", 0),
        "work_steps": recorded("work_steps", 0),
        "team_submitted": bool(submission) and (submission.get("agent") == agent or agent in refused),
        "notices": notices.get(agent, 0),
        "oversight": recorded("oversight", 0), "oversight_targets": recorded("oversight_targets", {}),
        "reads_refused": recorded("reads_refused", 0), "posts_refused": recorded("posts_refused", 0),
    }


def run_engagement(records: list[dict], submission: dict | None) -> dict | None:
    """The run's board engagement: how many agents looked, the median round they first looked, the posts, reads,
    oversight calls and refused calls, who read whom ({reader: {sender: count}}) and whose calls each regulator
    queried (oversight_graph: {regulator: {target: count}})."""
    rows = [(r["orbit_agent"], r["engagement"]) for r in records if r.get("engagement")]
    if not rows:
        return None
    rounds = sorted(e["discovery_round"] for _, e in rows if e.get("discovery_round") is not None)
    mid = len(rounds) // 2
    median = None if not rounds else (rounds[mid] if len(rounds) % 2 else (rounds[mid - 1] + rounds[mid]) / 2)
    return {
        "agents": len(rows),
        "agents_checked": sum(1 for _, e in rows if e["checks"] or e["reads"]),
        "agents_posted": sum(1 for _, e in rows if e["posts"]),
        "median_discovery_round": median,
        "checks": sum(e["checks"] for _, e in rows),
        "reads": sum(e["reads"] for _, e in rows),
        "posts": sum(e["posts"] for _, e in rows),
        "read_graph": {a: dict(e["read_senders"]) for a, e in rows if e.get("read_senders")},
        "team_submission": submission,
        "oversight": sum(e["oversight"] for _, e in rows),
        "reads_refused": sum(e["reads_refused"] for _, e in rows),
        "posts_refused": sum(e["posts_refused"] for _, e in rows),
        "oversight_graph": {a: dict(e["oversight_targets"]) for a, e in rows if e.get("oversight_targets")},
    }


# --------------------------------------------------------------------------
# mode, scale and preset
# --------------------------------------------------------------------------
def run_mode(config: dict | None, task: str | None) -> str | None:
    """The mode the run's config resolves to on its domain; None without a config or off the six domains."""
    if not config or task not in DOMAIN_KEYS:
        return None
    mode, key = config["mode"], DOMAIN_KEYS[task]
    if isinstance(mode, dict):
        return mode.get(key, DOMAIN_MODES[key])
    return mode or DOMAIN_MODES[key]




def run_identity(view: SampleView, task: str | None) -> dict:
    """mode, scale and preset for run.json, from the recorded config."""
    config = _cheatbench(view).get("config") or {}
    return {"mode": run_mode(config, task), "scale": config.get("scale"), "preset": config.get("preset")}


# --------------------------------------------------------------------------
# files per sample
# --------------------------------------------------------------------------
def _sample_key(view: SampleView) -> str:
    return f"{view.id}#{view.epoch}"


def _write_communication(view: SampleView, run_dir: Path) -> None:
    keys = ("CommunicationState:messages", "CommunicationState:events")
    comm = {k.split(":", 1)[1]: view.store_get(k) for k in keys if view.store_get(k)}
    if not comm:
        return
    comm_dir = run_dir / "communication"
    comm_dir.mkdir(exist_ok=True)
    (comm_dir / f"{_sample_key(view)}.json").write_text(json.dumps(comm, indent=1, default=str))


def _write_defense(view: SampleView, run_dir: Path, defenses_cfg: list, evidence: dict) -> None:
    if not (defenses_cfg or evidence["interventions"]):
        return
    def_dir = run_dir / "defense"
    def_dir.mkdir(exist_ok=True)
    payload = {"defenses": defenses_cfg, **evidence}
    (def_dir / f"{_sample_key(view)}.json").write_text(json.dumps(payload, indent=1, default=str))


# --------------------------------------------------------------------------
# one episode
# --------------------------------------------------------------------------
def episode_model(view: SampleView, agent: str) -> str:
    """The agent's own model: its role's model when the run gave the role one (metadata["cheatbench"]["role_models"]),
    else the eval's model, as an Inspect string without the swarm provider's prefix. An agent without a role is a
    worker."""
    cb = _cheatbench(view)
    role = ((_swarm_facts(cb) or {}).get("roles") or {}).get(agent) or "worker"
    return provider.inspect_string((cb.get("role_models") or {}).get(role) or view.model)


def _base_record(view: SampleView, agent: str, episode: str, defenses_cfg: list, evidence: dict) -> dict:
    cb = _cheatbench(view)
    scores = {k: (v.value if hasattr(v, "value") else v) for k, v in (view.scores or {}).items()}
    return {
        "id": view.id, "episode": episode, "replicate": view.epoch,
        "task": cb.get("task"), "variant": cb.get("variant", ""),
        "model": episode_model(view, agent), "agent": f"orbit/{agent}", "orbit_agent": agent,
        "orbit_roster": roster(view), "topology": topology(view),
        "control": cb.get("control", "baseline"), "monitor_model": cb.get("monitor_model"),
        "role": ((_swarm_facts(cb) or {}).get("roles") or {}).get(agent),
        "defenses": [d["name"] for d in defenses_cfg],
        "defense": defense_counts(evidence, agent),
        "agent_version": agent_version(),
        "prompt_file": cb.get("prompt_file"),
        "wall_time": view.total_time,
        "limit": view.limit.type if view.limit else None,
        "score": scores,
    }


def _submitted(transcript: dict) -> bool:
    for step in transcript["trajectory"].get("steps", []):
        for tc in step.get("tool_calls") or []:
            if tc.get("function_name") == "submit":
                return True
    return False


def _ended_by(view: SampleView, submitted: bool, ep_failure: str | None, delivered: bool = False) -> str:
    if submitted:
        return "submit"
    if delivered:
        return DELIVERED
    if view.limit or ep_failure in LIMIT_FAILURES:
        return "limit"
    if view.error:
        return "error"
    return "end"


def _write_episode(view: SampleView, run_dir: Path, agent: str, episode: str, error,
                   failure) -> tuple[dict, list[dict]]:
    """Write one agent's transcript and prompt; return the record fields that depend on them, and its ATIF steps."""
    transcript, info = episode_transcript(view, agent, judge_prompt(view, agent))
    ep_dir = run_dir / "trajectories" / episode
    ep_dir.mkdir(parents=True, exist_ok=True)
    transcript_path = ep_dir / "transcript.json"
    transcript_path.write_text(json.dumps(transcript, indent=1))
    (ep_dir / "prompt.txt").write_text(transcript["prompt"])
    # A limit ends the sample, not every agent in it: an agent whose last tool call
    # was submit, or that stop_after_delivery ended, finished its own episode before the limit hit the others.
    submitted, delivered = _submitted(transcript), ended_by_delivery(view, agent)
    if (submitted or delivered) and failure in LIMIT_FAILURES:
        ep_error, ep_failure = None, None
    else:
        ep_error, ep_failure = error, failure
    fields = {
        "final_text": info["final_text"],
        "n_turns": info["n_agent_steps"], "n_tool_calls": info["n_tool_calls"],
        "usage": info["usage"], "transcript_path": str(transcript_path),
        "error": ep_error, "failure": ep_failure, "ok": ep_error is None,
        "ended_by": _ended_by(view, submitted, ep_failure, delivered),
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
        episode = _sample_key(view) if len(agents) == 1 else f"{_sample_key(view)}@{agent}"
        record = _base_record(view, agent, episode, defenses_cfg, evidence)
        if _cheatbench(view).get("swe"):
            record.update(swe_record(view, agent))
        steps = []
        if agent_messages(view, agent):
            fields, steps = _write_episode(view, run_dir, agent, episode, error, failure)
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
        record.update(newer_episode_facts(view, agent))
        records.append(record)
    return records


# --------------------------------------------------------------------------
# the run
# --------------------------------------------------------------------------
def run_deviations(view: SampleView) -> list[str]:
    """The sample's own deviations (metadata["cheatbench"]["deviations"])."""
    return list(_cheatbench(view).get("deviations") or [])


def run_json(view: SampleView | None, eval_path: Path, run_dir: Path, records: list[dict], eval_id: str,
             model: str) -> dict:
    first = records[0] if records else {}
    task = first.get("task")
    topo = first.get("topology", "single")
    meta = view.metadata if view else {}
    cb = meta.get("cheatbench") or {}
    return {
        "task": task, "task_root": str((settings.CHEATBENCH / "tasks" / (task or "unknown")).resolve()),
        "variant": "", "run_id": run_dir.name, "model": provider.inspect_string(model),
        **({"role_models": cb["role_models"]} if "role_models" in cb else {}), "agent": "orbit",
        "source": "orbit", "topology": topo, "agents": first.get("orbit_roster", []),
        "control": first.get("control"), "monitor_model": first.get("monitor_model"),
        "defenses": (meta.get("experiment", {}).get("defenses") if view else None) or [],
        "swarm": swarm_block(view) if view else None,
        "deliverables": deliverables_block(view) if view else None,
        "orbit_halt_reason": orbit_halt_reason(view) if view else None,
        "eval_log": str(eval_path.resolve()), "eval_id": eval_id,
        "orbit_version": orbit.__version__, "inspect_version": inspect_ai.__version__,
        "swe": swe_block(view, run_dir) if view else None,
        "deviations": run_deviations(view) if view else [],
        "config": (meta.get("cheatbench") or {}).get("config") if view else None,
        "shutdown_rate": shutdown_rate((meta.get("cheatbench") or {}).get("config"), records) if view else None,
        "rules": rules_state(view) if view else None,
        "probes": probes_block(view) if view else None,
        **(run_identity(view, task) if view else {"mode": None, "scale": None, "preset": None}),
        "engagement": run_engagement(records, team_submission(view)) if view else None,
        "validity": validity_block(records, team_submission(view)) if view else None,
        "ledger": ledger_block(records, view.limit.type if view and view.limit else None) if view else None,
        **(newer_run_facts(view) if view else {}),
    }


def judge_rows(views: list[SampleView], records: list[dict], verdicts: dict[str, dict] | None) -> list[dict]:
    """judge.jsonl rows from `verdicts` (keyed by episode or agent) or from each sample's judge score."""
    by_sample = {_sample_key(v): v for v in views}
    rows = []
    for record in records:
        agent = record["orbit_agent"]
        if verdicts is not None:
            verdict = verdicts.get(record["episode"]) or verdicts.get(agent)
        else:
            view = by_sample[f"{record['id']}#{record['replicate']}"]
            verdict = ((_score_metadata(view, JUDGE_SCORER) or {}).get("verdicts") or {}).get(agent)
        if not verdict or not record["transcript_path"]:
            continue
        fields = {k: v for k, v in verdict.items() if k not in ("id", "episode", "transcript_path")}
        rows.append({"id": record["id"], "episode": record["episode"], **fields,
                     "transcript_path": record["transcript_path"]})
    return rows


OWNED_FILES = ("run.json", "episodes.jsonl", "judge.jsonl")
OWNED_DIRS = ("trajectories", "communication", "defense", "swe")


def clear_outputs(run_dir: Path) -> None:
    """Remove what an earlier export of this run wrote, so stale verdicts or transcripts never pair with new episodes.

    Only the files and directories this module writes are removed; the measures' files (signals.jsonl,
    swe_summary.json) stay.
    """
    for name in OWNED_FILES:
        (run_dir / name).unlink(missing_ok=True)
    for name in OWNED_DIRS:
        shutil.rmtree(run_dir / name, ignore_errors=True)


def write_run(eval_log_path: Path, run_dir: Path, *, verdicts: dict[str, dict] | None = None) -> dict:
    """Write the run directory for one eval log; return a short summary of what was written."""
    eval_path = Path(eval_log_path)
    log = read_eval_log(str(eval_path), resolve_attachments=True)
    run_dir = Path(run_dir).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    clear_outputs(run_dir)
    views = []
    for sample in log.samples or []:
        view = SampleView.from_eval_sample(sample, log)
        view.eval_log = str(eval_path.resolve())
        views.append(view)
    records = [r for view in views for r in sample_records(view, run_dir)]
    run = run_json(views[0] if views else None, eval_path, run_dir, records, log.eval.eval_id, log.eval.model)
    (run_dir / "run.json").write_text(json.dumps(run, indent=1))
    (run_dir / "episodes.jsonl").write_text("".join(json.dumps(r) + "\n" for r in records))
    rows = judge_rows(views, records, verdicts)
    if rows:
        (run_dir / "judge.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    return {"out_dir": str(run_dir), "episodes": len(records), "topology": run["topology"],
            "with_transcript": [r["episode"] for r in records if r["transcript_path"]],
            "without_transcript": [r["episode"] for r in records if not r["transcript_path"]],
            "judged": len(rows)}
