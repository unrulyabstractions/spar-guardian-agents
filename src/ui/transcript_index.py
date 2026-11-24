"""The transcript viewer's index: every valid run under runs/ with its row and its measures, read from what
`make process-transcripts` stored in each run's folder (src/measures/process_runs.py). The index computes nothing.

    idx = build_index(ROOT / "runs")                     # every listed run's stored row and labels
    idx["rows"][0]["levels"]["team"]                      # the team's level on each of the three ladders
    served_index(idx)                                     # what /api/index answers (SERVED)

Every folder under runs/ is a collection, except SKIP and folders that hold no run: a study or a manifest collection,
whose runs and their validity src/measures/run_listing.py list_collection reads. The index lists and serves the valid
runs only. A run of a task the study does not read (src/measures/ladder_by_domain.py HIDDEN_TASKS) is served, so it
opens, with hidden_task set on its row: the pages leave it out until the viewer's Settings turn its task on
(tv_settings.js), and `left_out` counts it.

For each run the index reads analysis/row.json (the `row` part, src/ui/viewer_parts.py) and the label files
(stored_measures): analysis/labels/cheating.json, coordination.json, collusion.json and judged.json (the judge labels
and CheatBench's verdicts). A run with no stored row is counted "not processed" and left out; one whose parts are
behind the registry (src/measures/stored_versions.py behind) is served as stored and counted "behind". build_row and
RunFiles are what the `row` part computes with.
"""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

from src.configuration.seed_packs import is_seeded
from src.configuration.settings import ROOT
from src.measures import judge_labels, stored_json, stored_versions
from src.measures.ladder_by_domain import HIDDEN_TASKS, TASK_DOMAIN, hidden_task
from src.measures.run_directory import RunDir, episode_agent, is_influencer, is_worker, roles_of, run_name
from src.measures.run_listing import Listed, list_collection
from src.measures.topology_fact import board_numbers
from src.ui import transcript_levels, transcript_presence

SKIP = ("study_smoke", "study_orbit2_check", "apart_controls")
"""Folders under runs/ the viewer never lists: mock smokes, a port check and the apart controls, whose runs are all
single agents."""
SERVED = ("built_at", "build_seconds", "pipeline", "collections", "errors", "rows", "left_out")
"""The index's keys /api/index answers; the index also keeps each run's measures and folder (entries)."""
LABELS = {"cheating": "cheating", "coordination": "coordination", "collusion": "collusion", "judged": "judged"}
"""The label parts the run page reads (stored_measures), by part name."""
HACKED = "hacked"
"""The Cheating rung whose runs show which exploit they used (exploit_shown)."""
PROCESS = "make process-transcripts"
"""The command that stores what the viewer serves."""

COLLECTION_ABOUT = {
    "ian_seed_prototyping_0": ("Ian seed prototyping 0", "seed packs",
                               "Teams of three on three targets, seeded with earlier teams' posts and files"),
    "ian_baseline_explorations_1": ("Ian baseline explorations 1", "teams, influencers",
                                    "Teams of three on four targets, with regulators and disregulators"),
    "ian_baseline_explorations_0": ("Ian baseline explorations 0", "October screen",
                                    "One agent, three, or three plus a regulator; five tasks"),
    "apart_controls": ("Apart controls", "lone agents", "Lone agents on nine tasks, five models, told to cheat or not"),
}
SYSTEM = "system"
"""The sender of a harness notice on the board."""
NAME_RE = re.compile(r"^(?P<stem>[a-z]+)_(?P<cell>.+)_[a-z]+\d+$")
"""A run's name, <stem>_<cell>_<tag><n>. The tag is no model: a check of the ORBIT 2.0 port is named
poem_minimal_regulated_orbit2. The models come from run.json alone (cell_of)."""
ROLE_MODELS = ("worker", "regulator", "disregulator")
INFLUENCER_LEVELS = {"regulator": "regulator_level", "disregulator": "disregulator_level"}
"""Each kind of influencer and the config field of its instruction level (src/configuration/swarm_config.py)."""
RULE_WORDS = {
    "generic": "generic honesty rule",
    "identified": "rule naming the exploit",
}
"""The workers' rule sentence (SwarmConfig.rule_level, src/configuration/instruction_levels.yaml `rule`) in the runs
table's words, every level but none. A stored run's level the ladder no longer holds reads "rule: <level>"."""


# ------------------------------------------------------------------------------------------------- the collections
def collections(runs: Path) -> list[Path]:
    """The collection folders under runs/: every folder but SKIP, the known ones first."""
    folders = [p for p in runs.iterdir() if p.is_dir() and p.name not in SKIP and not p.name.startswith(".")]
    known = list(COLLECTION_ABOUT)
    return sorted(folders, key=lambda p: (known.index(p.name) if p.name in known else len(known), p.name))


# ----------------------------------------------------------------------------------------------------- one run's data
def transcript_dirs(run_dir: Path) -> list[str]:
    """Trajectory folders that hold a transcript.json (names only; no transcript is opened)."""
    tdir = RunDir(run_dir).file("trajectories")
    if not tdir.is_dir():
        return []
    return sorted(n for n in os.listdir(tdir) if (tdir / n / "transcript.json").is_file())


def folder_agents(run_dir: Path, episodes: list[dict]) -> dict[str, str]:
    """agent -> trajectory folder. The folder is the episode name: <sample> alone, <sample>@<agent> in a team."""
    by_episode = {ep.get("episode"): episode_agent(ep) for ep in episodes}
    out = {}
    for name in transcript_dirs(run_dir):
        agent = by_episode.get(name)
        if agent is None and "@" in name:
            agent = name.rsplit("@", 1)[1]
        if agent is None and len(episodes) == 1:
            agent = episode_agent(episodes[0])
        if agent:
            out[agent] = name
    return out


def board_view(run_dir: Path, roles: dict) -> list[dict]:
    """Each sample's board (RunDir.boards): each message's id, its place among the board's posts (board_no, the #n
    agents see and the label P<n> the run page, the Board tab and the judges give it; topology_fact.board_numbers),
    whether it is a harness notice, whether a seed pack put it there (SwarmConfig.seed_pack: a previous team's post),
    its sender and the sender's role, and the tool calls that sent it and read it. A record of who came and went is no
    post: its board_no is None and it alone carries "record": True."""
    boards = []
    for c in RunDir(run_dir).boards():
        sent, reads = {}, {}
        for e in c.get("events") or []:
            for mid in e.get("message_ids") or []:
                if e.get("kind") == "sent":
                    sent[mid] = {"agent": e.get("agent_id"), "call_id": e.get("tool_call_id")}
                elif e.get("kind") == "read":
                    reads.setdefault(mid, []).append({"agent": e.get("agent_id"), "call_id": e.get("tool_call_id")})
        listed = c.get("messages") or []
        boards.append({"messages": [
            {"id": m.get("id"), "board_no": n, "notice": m.get("sender") == SYSTEM, "seeded": is_seeded(m),
             "sender": m.get("sender"),
             "role": roles.get(m.get("sender")), "sent": sent.get(m.get("id")), "reads": reads.get(m.get("id"), []),
             **({"record": True} if n is None else {})}
            for n, m in zip(board_numbers(listed), listed)]})
    return boards


@dataclass
class RunFiles:
    """One run's files as the index and the run page read them."""

    path: Path
    run_json: dict
    episodes: list[dict]
    sig_agents: dict[str, dict]
    roles: dict[str, str | None]

    @classmethod
    def read(cls, path: Path) -> RunFiles:
        run = RunDir(path)
        rj, episodes = run.run_json, run.episodes()
        sig_agents = {s.get("agent"): s for s in run.signals()}
        return cls(path, rj, episodes, sig_agents, roles_of(rj, episodes))

    @property
    def task(self) -> str | None:
        return self.run_json.get("task")

    def boards(self) -> list[dict]:
        return board_view(self.path, self.roles)

    def levels(self, measured: dict, boards: list[dict] | None = None) -> dict:
        """The three ladders (transcript_levels.compute) from the stored measures (stored_measures) and the board."""
        return transcript_levels.compute(self.sig_agents, self.episodes, self.boards() if boards is None else boards,
                                         self.roles, measured)


# ------------------------------------------------------------------------------------------------------ the judges
def cheatbench_by_agent(by_episode: dict[str, dict], episodes: list[dict]) -> dict[str, dict]:
    """One CheatBench judge's rows ({episode: row}) by the agent of the episode each reads."""
    return {episode_agent(ep): by_episode[ep.get("episode")] for ep in episodes if ep.get("episode") in by_episode}


def cheatbench_summary(judges: dict[str, dict[str, dict]], episodes: list[dict],
                       roles: dict[str, str | None]) -> list[dict]:
    """Each CheatBench judge's word on the team: {judge, judge_cheated (the workers it says cheated, its own
    `cheated`), of (the workers it gave a parsed verdict), workers}."""
    workers = [a for a, r in roles.items() if is_worker(r)]
    out = []
    for judge, rows in sorted(judges.items()):
        by_agent = cheatbench_by_agent(rows, episodes)
        parsed = [by_agent[a] for a in workers if a in by_agent and by_agent[a].get("parse_ok")
                  and isinstance(by_agent[a].get("cheated"), bool)]
        out.append({"judge": judge, "judge_cheated": sum(r["cheated"] for r in parsed), "of": len(parsed),
                    "workers": len(workers)})
    return out


def label_summary(labels: dict, roles: dict[str, str | None]) -> dict:
    """The judge labels as the runs table shows them, from the measure's answer (judge_labels.labels): {"judge", "team":
    {label: {"score": the workers' mean score, rounded, "n": the workers scored, "max": the highest worker score,
    "agent": the first worker with it, "kind": that agent's kind} or None},
    "influencers": [{agent, role, label: its score or None, "kinds": {label: its kind}}]}. None is an agent or a team
    the judge has not scored. A kind is manipulation's (mislead, control or none), None for disclosure: the page marks
    a mislead score, which an independent reading found untrustworthy."""
    agents = labels["agents"]

    def score(agent: str, label: str) -> int | None:
        row = agents.get(agent)
        return row[label]["score"] if row else None

    def kind(agent: str | None, label: str) -> str | None:
        row = agents.get(agent) if agent else None
        return row[label].get("kind") if row else None
    team = {}
    for label in judge_labels.LABELS:
        top = labels["team"].get(label)
        scored = [s for a in agents if is_worker(roles.get(a)) and (s := score(a, label)) is not None]
        who = next((a for a in agents if is_worker(roles.get(a)) and score(a, label) == top), None)
        team[label] = None if top is None or not scored else {
            "score": int(sum(scored) / len(scored) + 0.5), "n": len(scored), "max": top, "agent": who,
            "kind": kind(who, label)}
    influencers = [{"agent": a, "role": roles.get(a), **{label: score(a, label) for label in judge_labels.LABELS},
                    "kinds": {label: kind(a, label) for label in judge_labels.LABELS}}
                   for a in labels["influencers"]]
    return {"judge": labels["judge"], "team": team, "influencers": influencers}


# ------------------------------------------------------------------------------------------------------- the rows
def short_model(model: str | None) -> str | None:
    """A model string's last part without a date suffix: openrouter/deepseek/deepseek-v4.1-flash-1017 gives
    deepseek-v4.1-flash."""
    if not model:
        return None
    return re.sub(r"-\d{4}$", "", model.split("/")[-1])


@dataclass
class Cell:
    """What a run was made with: its task's short name, its cell, and the models of its workers and influencers."""

    stem: str | None
    cell: str | None
    worker: str | None
    regulator: str | None
    disregulator: str | None


CELL_SIZES = ("solo", "duo", "minimal")
"""The team-size word that begins a cell's name in its script's (flipt_solo_cheat, ian_flipt_minimal_regulated)."""


def module_cell(module: str) -> tuple[str | None, str | None]:
    """The stem and the cell of a cell's script, from its module: the cell begins at the first team-size word after
    the stem (`studies.cells.swe.ian_flipt_minimal_cheat` gives ian_flipt and minimal_cheat); a script with no such
    word is cut at its first underscore."""
    words = module.rsplit(".", 1)[-1].split("_")
    at = next((i for i, word in enumerate(words) if i and word in CELL_SIZES), 1)
    return "_".join(words[:at]) or None, "_".join(words[at:]) or None


def cell_of(listed: Listed, rf: RunFiles) -> Cell:
    """The task's stem and the cell from the manifest row, the INDEX.jsonl row, else the script that launched the run
    (run.json launch.module, `studies.cells.swe.ian_flipt_minimal_cheat`), else the run's name (NAME_RE). The script
    comes before the name: a run of a study plan is named after its target (ian_flipt_haiku_positive_control_...),
    not its cell, and such a name still parses. The models come from run.json alone: a role's own model in
    role_models, else the eval's model, which a role with no model of its own runs
    (src/configuration/swarm_config.py role_models)."""
    rj, meta = rf.run_json, listed.meta
    role_models = rj.get("role_models") or {}
    models = [short_model(role_models.get(role) or rj.get("model")) for role in ROLE_MODELS]
    if listed.source == "manifest":
        return Cell(meta["task"], "solo_cheat" if meta["cell"] == "cheat" else meta["cell"], *models)
    if meta:
        return Cell(module_cell(meta.get("cell") or "")[0], meta.get("kind"), *models)
    if module := (rj.get("launch") or {}).get("module"):
        return Cell(*module_cell(str(module)), *models)
    if m := NAME_RE.match(run_name(listed.path)):
        return Cell(m["stem"], m["cell"], *models)
    return Cell(None, None, *models)


def task_label(task: str | None, stem: str | None) -> str:
    """The run's task in the pages' words: its domain (ladder_by_domain.TASK_DOMAIN, the tasks the study reads) and its
    instance, as "SWE (flipt-02e2)" (the sample's id, which every run records; the stem of its cell for a run with no
    episode); the task's name for a task with no domain."""
    if task in TASK_DOMAIN:
        return f"{TASK_DOMAIN[task]} ({stem})" if stem else TASK_DOMAIN[task]
    return (task or stem or "?").replace("_", " ")


def worker_conditioning(cell: Cell, cfg: dict) -> str:
    """What the workers' system prompt told them, from the run's configuration: told to cheat (the cell's cheat
    instruction), a rule sentence (rule_level, RULE_WORDS), both, or "baseline" (nothing of either)."""
    parts = ["told to cheat"] if "cheat" in (cell.cell or "").split("_") else []
    if cfg.get("rule_level") not in (None, "none"):
        parts.append(RULE_WORDS.get(cfg["rule_level"], f"rule: {cfg['rule_level']}"))
    return "; ".join(parts) or "baseline"


def influencer_conditioning(cell: Cell, cfg: dict, roles: dict[str, str | None]) -> list[dict]:
    """Each kind of influencer the run had: {role (regulator or disregulator), n (its agents in the run's roles), level
    (its instruction level in the configuration, INFLUENCER_LEVELS), model (the model it ran, cell_of)}; [] for a run
    with no influencer."""
    models = {"regulator": cell.regulator, "disregulator": cell.disregulator}
    out = []
    for role, level_key in INFLUENCER_LEVELS.items():
        n = sum(1 for r in roles.values() if r == role)
        if n:
            out.append({"role": role, "n": n, "level": cfg.get(level_key), "model": models[role]})
    return out


def influencer_words(influencers: list[dict]) -> str:
    """The influencers in words, "regulator (generic, claude-haiku-5.5)", or "no influencer"."""
    words = []
    for x in influencers:
        who = x["role"] if x["n"] == 1 else f"{x['n']} {x['role']}s"
        what = ", ".join(v for v in (x["level"], x["model"]) if v)
        words.append(f"{who} ({what})" if what else who)
    return "; ".join(words) or "no influencer"


def agents_words(n_agents: int, roles: dict) -> tuple[str, int]:
    """Who ran, in words, and the number of workers: "One agent", or "3 workers" and the influencers' count
    (is_influencer)."""
    influencers = sum(1 for r in roles.values() if is_influencer(r))
    workers = n_agents - influencers
    if n_agents <= 1:
        return "One agent", workers
    extra = f", {influencers} influencer{'' if influencers == 1 else 's'}" if influencers else ""
    return f"{workers} workers{extra}", workers


INTEREST = ("no cheating, with a regulator", "hacked, with a disregulator", "hacked, with a regulator",
            "no cheating, with a disregulator", "both influencers", "hacked, no influencer", "attempted",
            "no cheating, no influencer")
"""The runs page's default order, most interesting first (the user's ranking of 2026-11-19, with "both" placed by the
user later that day): a team's Cheating rung (hacked, attempted, or no cheating: none, looked, tempted) against the
influencers it ran with. A run with a regulator and a disregulator is "both influencers", whatever its rung."""


def interest(row: dict) -> dict:
    """Where the run stands in INTEREST: {"rank" (1 is the most interesting), "why" (the class in words)}."""
    rung = row["levels"]["team"].get("cheating")
    kinds = {x["role"] for x in row["influencer_conditioning"]}
    regulator, disregulator = "regulator" in kinds, "disregulator" in kinds
    if regulator and disregulator:
        why = "both influencers"
    elif rung == "attempted":
        why = "attempted"
    elif rung == "hacked":
        why = ("hacked, with a disregulator" if disregulator else "hacked, with a regulator" if regulator
               else "hacked, no influencer")
    else:
        why = ("no cheating, with a regulator" if regulator else "no cheating, with a disregulator" if disregulator
               else "no cheating, no influencer")
    return {"rank": INTEREST.index(why) + 1, "why": why}


def interest_order(row: dict) -> tuple:
    """The sort key of the default order: the INTEREST rank, then, each from high to low, the number of workers, the
    number of influencers, the team's Coordination level and its Collusion rung; the collection and the run last."""
    def level(ladder: str) -> int:
        levels, value = transcript_levels.LADDERS[ladder]["levels"], row["levels"]["team"].get(ladder)
        return levels.index(value) if value in levels else -1
    influencers = sum(x["n"] for x in row["influencer_conditioning"])
    return (interest(row)["rank"], -row["workers"], -influencers, -level("coordination"), -level("collusion"),
            row["col"], row["run"] or "")


def started(run_json: dict) -> str:
    """When the run started, from its eval log's name ("2026-11-22T13-25-43-00-00_..."): "2026-11-22T13-25-43", or ""
    when the name holds no time."""
    m = re.match(r"(\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2})", Path(str(run_json.get("eval_log") or "")).name)
    return m.group(1) if m else ""


def rounds_used(run_json: dict) -> int | None:
    """The rounds the run used: the most any agent used (run.json rules.rounds_used); None when not recorded."""
    used = [n for n in ((run_json.get("rules") or {}).get("rounds_used") or {}).values() if isinstance(n, int)]
    return max(used) if used else None


def round_span(run_json: dict) -> dict | None:
    """The most and the fewest rounds an agent used, each with how the agents at that count left:
    {"longest": {"rounds": 9, "why": ["stop", None]}, "shortest": {"rounds": 7, "why": ["submit"]}}. why holds
    each distinct reason of run.json rules.exits (transcript_presence.EXIT_WHY) once, in EXIT_WHY's order, and None
    last for an agent the run's end stopped. None when the run recorded no rounds or no exits (a run before
    presence was recorded)."""
    rules = run_json.get("rules") or {}
    used = {a: n for a, n in (rules.get("rounds_used") or {}).items() if isinstance(n, int)}
    if not used or "exits" not in rules:
        return None
    exits = rules.get("exits") or {}

    def at(count: int) -> dict:
        whys = {(exits.get(a) or {}).get("why") for a, n in used.items() if n == count}
        order = [w for w in transcript_presence.EXIT_WHY if w in whys]
        other = sorted(w for w in whys if w and w not in order)
        return {"rounds": count, "why": order + other + [None] * (None in whys)}
    return {"longest": at(max(used.values())), "shortest": at(min(used.values()))}


def exploit_shown(cheating: dict) -> str | None:
    """The Cheating label's exploit_used (ladder_domain.Domain.exploit_used) on a run whose team is hacked; None on
    every other run and on a label stored before the field."""
    return cheating.get("exploit_used") if cheating.get("team") == HACKED else None


def build_row(col: str, listed: Listed, rf: RunFiles, measured: dict) -> dict:
    """The run's index row from its files and its measures (RunFiles.measured's answer)."""
    rj, cfg = rf.run_json, rf.run_json.get("config") or {}
    n_agents = len(rf.episodes) or int(cfg.get("n_agents") or 1)
    cell = cell_of(listed, rf)
    told = worker_conditioning(cell, cfg)
    influencers = influencer_conditioning(cell, cfg, rf.roles)
    sample = (rf.episodes[0].get("episode") if rf.episodes else "") or ""
    agents, workers = agents_words(n_agents, rf.roles)
    variant = (rj.get("swe") or {}).get("variant") or (rj.get("creative") or {}).get("variant")
    return {
        "id": f"{col}/{listed.key}", "col": col, "key": listed.key,
        "run": run_name(listed.path) if listed.source != "manifest" else listed.meta.get("key"),
        "task_name": rf.task,
        "task_label": task_label(rf.task, variant or sample.split("#")[0] or cell.stem),
        "instance": sample.split("#")[0] or None,
        "arm_label": f"{agents}; workers {told}; {influencer_words(influencers)}", "agents": agents,
        "worker_conditioning": told, "influencer_conditioning": influencers, "model": cell.worker,
        "model_id": (rj.get("role_models") or {}).get("worker") or rj.get("model"), "n_agents": n_agents,
        "workers": workers, "seed": listed.meta.get("seed", cfg.get("seed")),
        "seed_pack": cfg.get("seed_pack") or "none", "started": started(rj), "rounds": rounds_used(rj),
        "round_span": round_span(rj),
        "ended_by": ((rj.get("signals") or {}).get("team") or {}).get("ended_by"),
        "exploit_used": exploit_shown(measured["cheating"]),
        "levels": transcript_levels.index_levels(rf.levels(measured), rf.roles),
        "cheatbench": cheatbench_summary(measured["cheatbench"], rf.episodes, rf.roles),
        "labels": label_summary(measured["labels"], rf.roles),
    }


def read_stored(path: Path):
    """A stored part's value (src/measures/stored_json.py)."""
    return stored_json.decode(path.name, path.read_bytes())


def stored_measures(run: RunDir) -> dict:
    """The run's measures as the run page reads them, from its stored label files: CheatBench's verdicts ({judge:
    {episode: row}}), the three ladders' measures and each agent's judge labels. Raises FileNotFoundError for a run
    whose labels are not stored."""
    stored = {name: read_stored(run.output(part)) for name, part in LABELS.items()}
    judged = stored.pop("judged")
    return {"cheatbench": judged["cheatbench"], **stored, "labels": judged["labels"]}


def stored_entry(listed: Listed) -> dict:
    """One run's entry: {"row", "measured", "dir", "behind"} (its stored row and labels, its folder, {part: why} of
    its parts behind the registry), {"missing": True} for a run not processed, or {"error"} for one whose stored files
    cannot be read."""
    run = RunDir(listed.path)
    try:
        path = run.output("row")
        if not path.is_file():
            return {"missing": True}
        return {"row": read_stored(path), "measured": stored_measures(run), "dir": str(listed.path),
                "behind": stored_versions.behind(run)}
    except FileNotFoundError:
        return {"missing": True}
    except (OSError, ValueError, KeyError, TypeError) as e:
        return {"error": f"{type(e).__name__}: {e}"}


def valid_runs(runs: Path) -> list[tuple[str, list[Listed]]]:
    """Each collection's valid runs (run_listing's status), the collections without one left out."""
    out = []
    for folder in collections(runs):
        listed = [item for item in list_collection(folder) if item.status == "valid"]
        if listed:
            out.append((folder.name, listed))
    return out


def pipeline_counts(entries: dict[str, dict]) -> dict:
    """{"not_processed", "behind": the runs with a part behind, "parts": {part: runs behind on it}, "command"}."""
    parts: dict[str, int] = {}
    for entry in entries.values():
        for name in entry.get("behind") or {}:
            parts[name] = parts.get(name, 0) + 1
    return {"not_processed": sum(bool(e.get("missing")) for e in entries.values()),
            "behind": sum(bool(e.get("behind")) for e in entries.values()), "parts": parts, "command": PROCESS}


def build_index(runs: Path) -> dict:
    """Every collection's valid runs, each with its stored row and measures; a run of a task the study does not read
    has hidden_task on its row, is counted in `left_out` and in its collection's other_tasks, not in its runs."""
    t0 = time.time()
    cols = valid_runs(runs)
    entries = {f"{name}/{item.key}": stored_entry(item) for name, listed in cols for item in listed}
    rows, errors, collections_out, left_out, single = [], [], [], 0, 0
    for name, listed in cols:
        label, tag, about = COLLECTION_ABOUT.get(name, (name, "", ""))
        shown = other = 0
        latest = ""
        for item in listed:
            entry = entries[f"{name}/{item.key}"]
            latest = max(latest, (entry.get("row") or {}).get("started") or "")
            if "error" in entry:
                errors.append({"id": f"{name}/{item.key}", "error": entry["error"]})
            elif entry.get("missing"):
                continue
            elif single_agent(entry["row"]):
                single += 1
            else:
                hidden = hidden_task(entry["row"]["task_name"])
                rows.append({**entry["row"], "interest": interest(entry["row"]), "hidden_task": hidden})
                other += hidden
                shown += not hidden
        left_out += other
        if shown or other:
            collections_out.append({"key": name, "label": label, "tag": tag, "about": about, "runs": shown,
                                    "other_tasks": other, "latest": latest})
    collections_out.sort(key=lambda c: c["latest"], reverse=True)  # newest first; a collection with no time last
    rows.sort(key=interest_order)
    return {"runs": str(runs), "built_at": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
            "build_seconds": round(time.time() - t0, 2), "pipeline": pipeline_counts(entries),
            "collections": collections_out, "errors": errors, "rows": rows,
            "left_out": {"tasks": sorted(HIDDEN_TASKS), "runs": left_out, "single_agent": single},
            "entries": entries}


def single_agent(row: dict) -> bool:
    """Whether the run had one agent: one worker and no influencer. The viewer lists teams only."""
    return row["workers"] + sum(x.get("n", 1) for x in row["influencer_conditioning"]) == 1


def served_index(idx: dict) -> dict:
    """The index as /api/index serves it: its SERVED keys, without the measures, folders and stamps the cache keeps."""
    return {k: idx[k] for k in SERVED}


def rel(path: Path) -> str:
    """`path` from the repository root when under it, for shorter lines."""
    try:
        return str(Path(path).resolve().relative_to(ROOT))
    except ValueError:
        return str(path)
