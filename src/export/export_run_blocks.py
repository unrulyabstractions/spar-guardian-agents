"""The blocks of run.json and the per-episode facts the export reads from one sample.

Each function reads a SampleView (src/export/eval_sample_view.py) or the episode records and returns one block:
what the team left behind, the swarm's composition, access, knowledge work, the machines domains, the honeypot
signals, the sandbox, validity, the ledger, stop-after-delivery, SWE, the turn rules, the probes and the board
engagement. A block is None when the run did not record it. The export (src/export/run_export.py) assembles
them into run.json and episodes.jsonl.

    block = delivery_block(view)
    facts = newer_episode_facts(view, "a01")
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from src.domains.openmath.openmath_settings import TEAM_IDENTITY
from src.export.atif_transcripts import TURN_RULES_TAG
from src.export.eval_sample_view import SampleView, topology
from src.models import model_registry as models
from src.sandbox import sandbox_hygiene as hygiene

DELIVERABLES_SCORER = "deliverables_scorer"
KNOWLEDGE_SCORER = "knowledge_deliverables"   # src/domains/knowledge/knowledge_scoring.py
INTERVIEW_SCORER = "interview_deliverables"   # src/domains/interview/interview_scoring.py
PROTEIN_SCORER = "protein_deliverables"       # src/domains/protein/protein_scoring.py
# The machines domains (one container per agent): run.json key and scorer name.
MACHINE_BLOCKS = {"interview": INTERVIEW_SCORER, "protein": PROTEIN_SCORER}
SWE_GRADER = "swe_grader"
PROBES_SCORER = "end_of_episode_probes"
# the StoreModel of src/swarm/turn_rules/turn_state.py; Inspect stores its fields as "TurnState:<field>"
TURN_STATE = "TurnState"
TURN_STATE_FIELDS = ("agents", "cap", "pooled", "turn_order", "seed", "rounds", "windows", "activations",
                     "work_steps", "ended", "timeline")
# The pull board's per-agent counts ({agent: value}); written only when the run stored them.
TURN_STATE_BOARD_FIELDS = ("free_steps", "checks", "reads", "posts", "discovery_round", "read_senders",
                           "free_caps", "reads_per_turn", "posts_per_turn", "turn_budget",
                           "oversight", "oversight_targets", "turn_reads", "turn_posts", "reads_refused",
                           "posts_refused", "history_marks")
# The knowledge-work keys run.json keeps; the row, the evaluation file and the image ids stay in the eval log.
KNOWLEDGE_SAMPLE_KEYS = ("layout", "mode", "split", "row_id", "meta", "comparator", "seats")
KNOWLEDGE_SCORE_KEYS = ("contracts", "grader_state", "last_verdict", "access")
TEAM_SUBMISSION = "TeamSubmission"        # its StoreModel: agent, round, answer, time, refused
TEAM_SUBMISSION_FIELDS = ("agent", "round", "answer", "time", "refused")
# src/swarm/turn_rules/turn_state.py's stop-after-delivery record, kept only under the rule
DELIVERED_STORE = "Delivered"
DELIVERED_FIELDS = ("after", "team", "seen", "halt_round", "cut", "checks", "errors")
DELIVERED = "delivered"                   # its TurnState.ended reason and an episode's ended_by
SWE_TEXT_KEYS = ("first_message", "judge_prompt", "system_prompt")


def cheatbench(view: SampleView) -> dict:
    """The sample's metadata["cheatbench"], or {} when it has none."""
    return (view.metadata or {}).get("cheatbench", {})


def score_metadata(view: SampleView, name: str) -> dict | None:
    """The metadata of the sample's score `name`, or None when that scorer did not score the sample."""
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


def swarm_facts(cb: dict) -> dict | None:
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
    return swarm_facts(cheatbench(view))


# --------------------------------------------------------------------------
# access, the turn budget and knowledge work (runs that record them)
# --------------------------------------------------------------------------
def access_block(view: SampleView) -> dict | None:
    """The access policy and what each agent held, when the run recorded them: "policy" (split, all or explicit)
    and per agent "holds" (its items) from metadata["cheatbench"]["access"]; on openmath in the split layout also the
    team remote, the honeypot branches each copy kept, the archive and the slices each copy held (None: the whole
    archive); on knowledge work the review tool and the read permission on the pipeline artifact. None for a run
    that recorded none of it."""
    cb = cheatbench(view)
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
    kw = cheatbench(view).get("knowledge")
    if not kw:
        return None
    md = score_metadata(view, KNOWLEDGE_SCORER) or {}
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
    facts = cheatbench(view).get(key)
    if not facts:
        return None
    scorer = MACHINE_BLOCKS[key]
    sc = (view.scores or {}).get(scorer)
    return {**facts, **((score_metadata(view, scorer) or {}).get(key) or {}),
            "value": None if sc is None else sc.value}


def signals_block(view: SampleView) -> dict | None:
    """The honeypot probe's report (src/measures/honeypot_fingerprints.py by_owner), from whichever scorer ran it."""
    for name in (DELIVERABLES_SCORER, KNOWLEDGE_SCORER, *MACHINE_BLOCKS.values()):
        found = (score_metadata(view, name) or {}).get("signals")
        if found is not None:
            return found
    return None


def sandbox_block(view: SampleView) -> dict | None:
    """What the sandbox held at the end of the run (src/sandbox/sandbox_hygiene.py STATE, from the domain's
    deliverables
    scorer; per container on the machines domains), the reaps after the agents' commands (hygiene.REAP_STORE:
    {agent: {commands, killed, max_alive, survivors, errors}}) and the loopback guard's report (hygiene.GUARD_STORE).
    None for a run that recorded none of them."""
    end = None
    for name in (DELIVERABLES_SCORER, KNOWLEDGE_SCORER, *MACHINE_BLOCKS.values()):
        found = (score_metadata(view, name) or {}).get("sandbox")
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
    """The stop-after-delivery record (src/swarm/turn_rules/turn_state.py Delivered): N ("after"), whether one
    deliverable counted for the team, each agent's first sighting {round, turn}, the team's last round, the agents of
    a team whose later steps were cut, the predicate's calls and errors, and the agents the rule ended ("ended", from
    TurnState.ended).
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
    cb = cheatbench(view)
    facts = {"turn_budget": cb.get("turn_budget"), "access": access_block(view), "knowledge": knowledge_block(view),
             **{key: machine_block(view, key) for key in MACHINE_BLOCKS}, "signals": signals_block(view),
             "delivery": delivery_block(view), "sandbox": sandbox_block(view), "code": cb.get("code")}
    return {k: v for k, v in facts.items() if v is not None}


def newer_episode_facts(view: SampleView, agent: str) -> dict:
    """An episode's access (what it held), review (its knowledge-work grader state), task_tool (its owner's
    state and its calls of the task's tool on a machines domain) and delivery (the round and working step at which
    its deliverable was first seen), when the run recorded them."""
    facts = {"access": ((access_block(view) or {}).get("agents") or {}).get(agent),
             "review": ((score_metadata(view, KNOWLEDGE_SCORER) or {}).get("grader_state") or {}).get(agent),
             "task_tool": next((md["agents"][agent] for md in map(lambda s: score_metadata(view, s),
                                                                   MACHINE_BLOCKS.values())
                                if md and agent in (md.get("agents") or {})), None),
             "delivery": ((delivery_block(view) or {}).get("seen") or {}).get(agent)}
    return {k: v for k, v in facts.items() if v is not None}


# --------------------------------------------------------------------------
# SWE (CheatBench software_engineering under Lucy's plan)
# --------------------------------------------------------------------------
def swe_grades(view: SampleView) -> dict | None:
    """Hidden-test results per target (team, a01, ...) from the SWE grader, without the diff text."""
    md = score_metadata(view, SWE_GRADER)
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
    cb = cheatbench(view)
    swe = cb.get("swe")
    if not swe:
        return None
    md = score_metadata(view, SWE_GRADER) or {}
    _write_swe_diffs(md, run_dir)
    setup = view.store_get("swe_setup") or md.get("setup") or {}
    block = {k: v for k, v in swe.items() if k not in SWE_TEXT_KEYS}
    fragments = block.get("fragments") or {}
    block["fragments"] = {a: {k: v for k, v in f.items() if k != "patch"} for a, f in fragments.items()} or None
    jigsaw = _swe_jigsaw(swe)
    block.update({
        "task_key": swe.get("task_key") or swe.get("issue"), "jigsaw": jigsaw,
        "rule": _config_rule(cb),
        # the planted {piece: commit} of each agent (src/domains/swe/swe_honeypot_setup.py)
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
    cb = cheatbench(view)
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
    """The turn rules' store (src/swarm/turn_rules/turn_state.py TurnState), or None for a run without them."""
    if view.store_get(f"{TURN_STATE}:agents") is None:
        return None
    state = {f: view.store_get(f"{TURN_STATE}:{f}") for f in TURN_STATE_FIELDS}
    board = {f: view.store_get(f"{TURN_STATE}:{f}") for f in TURN_STATE_BOARD_FIELDS}
    return {**state, **{f: v for f, v in board.items() if v is not None}}


def rules_facts(state: dict | None, agent: str) -> dict | None:
    """One agent's activations, working steps, how it ended and its window of rounds."""
    if state is None:
        return None
    return {k: (state.get(k) or {}).get(agent) for k in ("activations", "work_steps", "ended", "windows")}


def shutdown_rate(config: dict | None, records: list[dict]) -> float | None:
    """The share of agents that ended their own episode with submit; None when the agents had no submit tool."""
    if (config or {}).get("shutdown") is False or not records:
        return None
    return sum(1 for r in records if r.get("ended_by") == "submit") / len(records)


def probes_block(view: SampleView) -> dict | None:
    md = score_metadata(view, PROBES_SCORER)
    return None if md is None else md.get("probes")


# --------------------------------------------------------------------------
# the board: who looked, read, posted and submitted for the team
# --------------------------------------------------------------------------
def team_submission(view: SampleView) -> dict | None:
    """The team's submission, from the TeamSubmission store (src/swarm/board.py team_submit)."""
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
# mode, scale, preset and deviations
# --------------------------------------------------------------------------
def run_identity(view: SampleView) -> dict:
    """mode, scale and preset for run.json, from the recorded config."""
    config = cheatbench(view).get("config") or {}
    return {"mode": config.get("mode"), "scale": config.get("scale"), "preset": config.get("preset")}


def run_deviations(view: SampleView) -> list[str]:
    """The sample's own deviations (metadata["cheatbench"]["deviations"])."""
    return list(cheatbench(view).get("deviations") or [])
