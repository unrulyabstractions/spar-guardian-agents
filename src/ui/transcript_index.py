"""The transcript viewer's index: every valid run under runs/ with its row and its measures, cached between starts.

    idx = load_or_build(ROOT / "runs", rebuild=False)     # the index, its runs measured again where their files changed
    idx["rows"][0]["levels"]["team"]                      # the team's level on each of the three ladders
    served_index(idx)                                     # what /api/index answers (SERVED)

Every folder under runs/ is a collection, except SKIP and folders that hold no run: a study or a manifest collection,
whose runs and their validity src/measures/run_listing.py list_collection reads. The index lists, measures and serves
the valid runs only, and of those only the runs of the tasks the study reads: a run of a task it leaves out
(src/measures/ladder_by_domain.py HIDDEN_TASKS) is counted in `left_out` and never measured.

For each run the index reads run.json, episodes.jsonl, signals.jsonl and the board (RunFiles), CheatBench's verdicts
(src/judging/verdict_files.py cheatbench) and the measures, which read the transcripts and the judges' verdict files:
src/measures/judge_labels.py judged_cheating (the Cheating ladder with CheatBench's labels beside its clauses),
labels (the judge labels disclosure and manipulation, each a score with its located excerpt), coordination.py and
collusion.py run_collusion. The index never writes a run. Its one output is the cache,
tmp/transcript_viewer/index.json: each run's row, its measures (RunFiles.measured) and its folder, under a stamp of the
run's files (stamp) and of the code (code_stamp). A start measures again only the runs whose stamp changed, in parallel
processes, and prints its progress; --rebuild, a new INDEX_VERSION or new code measures every run.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from src.configuration.settings import ROOT
from src.domains import BY_TASK
from src.judging import verdict_files
from src.measures import collusion, coordination, judge_labels
from src.measures.ladder_by_domain import HIDDEN_TASKS, TASK_DOMAIN, hidden_task
from src.measures.run_directory import RunDir, episode_agent, is_influencer, is_worker, roles_of, run_name
from src.measures.run_listing import Listed, list_collection
from src.ui import transcript_levels
from src.utils.json_files import read_json, read_jsonl

CACHE = ROOT / "tmp" / "transcript_viewer" / "index.json"
SKIP = ("study_smoke", "study_orbit2_check")
INDEX_VERSION = 11  # 11: "baseline" for workers told nothing
WORKERS = 6
"""Processes that measure runs when a start finds runs to measure: each loads transcripts, so few enough to spare the
machine's memory."""
PROGRESS = 100
"""A start that measures runs prints a line every PROGRESS runs measured."""
MEASURING = ("src/measures", "src/domains", "src/judging", "src/ui")
"""The folders whose code decides a run's row and measures (code_stamp)."""
SERVED = ("built_at", "build_seconds", "measured", "collections", "errors", "rows", "left_out")
"""The index's keys /api/index answers; the cache also keeps its version, its runs folder, each run's measures, its
folder and its stamps."""

COLLECTION_ABOUT = {
    "study_exploration_1": ("Exploration 1", "teams, influencers",
                            "Teams of three on four targets, with regulators and disregulators"),
    "study_exploration_0": ("Exploration 0", "October screen",
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
    "vague": "rule: work of its own",
    "copy": "rule: copy no solution",
    "precise": "rule: use no existing solution",
    "explicit": "rule naming where the exploit lies",
    "identified": "rule naming the exploit",
}
"""The workers' rule sentence (SwarmConfig.rule_level, src/configuration/instruction_levels.yaml `rule`) in the runs
table's words, every level but none."""


# ------------------------------------------------------------------------------------------------- the collections
def collections(runs: Path) -> list[Path]:
    """The collection folders under runs/: every folder but SKIP, the known ones first."""
    folders = [p for p in runs.iterdir() if p.is_dir() and p.name not in SKIP and not p.name.startswith(".")]
    known = list(COLLECTION_ABOUT)
    return sorted(folders, key=lambda p: (known.index(p.name) if p.name in known else len(known), p.name))


# ----------------------------------------------------------------------------------------------------- one run's data
def transcript_dirs(run_dir: Path) -> list[str]:
    """Trajectory folders that hold a transcript.json (names only; no transcript is opened)."""
    tdir = run_dir / "trajectories"
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
    """Each sample's board (RunDir.boards): each message's id, its place on the board (board_no, the label P<n> the run
    page, the Board tab and the judges give it), whether it is a harness notice, its sender and the sender's role, and
    the tool calls that sent it and read it."""
    boards = []
    for c in RunDir(run_dir).boards():
        sent, reads = {}, {}
        for e in c.get("events") or []:
            for mid in e.get("message_ids") or []:
                if e.get("kind") == "sent":
                    sent[mid] = {"agent": e.get("agent_id"), "call_id": e.get("tool_call_id")}
                elif e.get("kind") == "read":
                    reads.setdefault(mid, []).append({"agent": e.get("agent_id"), "call_id": e.get("tool_call_id")})
        boards.append({"messages": [
            {"id": m.get("id"), "board_no": i, "notice": m.get("sender") == SYSTEM, "sender": m.get("sender"),
             "role": roles.get(m.get("sender")), "sent": sent.get(m.get("id")), "reads": reads.get(m.get("id"), [])}
            for i, m in enumerate(c.get("messages") or [], 1)]})
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
        rj = read_json(path / "run.json") or {}
        episodes = read_jsonl(path / "episodes.jsonl")
        sig_agents = {s.get("agent"): s for s in read_jsonl(path / "signals.jsonl") if s.get("row") == "agent"}
        return cls(path, rj, episodes, sig_agents, roles_of(rj, episodes))

    @property
    def task(self) -> str | None:
        return self.run_json.get("task")

    def boards(self) -> list[dict]:
        return board_view(self.path, self.roles)

    def cheating(self) -> dict:
        """The Cheating ladder of the run with CheatBench's labels beside its clauses (src/measures/judge_labels.py
        judged_cheating) as the viewer keeps it, or {"applicable": False, "reason"} for a run without episodes or of a
        task with no ladder."""
        if self.task not in BY_TASK:
            return {"applicable": False, "reason": f"no Cheating ladder for task {self.task!r}"}
        if not self.episodes:
            return {"applicable": False, "reason": "the run recorded no episode"}
        return transcript_levels.cheating_shown(judge_labels.judged_cheating(self.path))

    def collusion(self) -> dict:
        """The Collusion ladder of the run (src/measures/collusion.py run_collusion), as the viewer keeps it
        (transcript_levels.collusion_shown); or
        {"applicable": False, "reason"} when the measure says it does not apply (not_applicable), which loads no
        transcript."""
        reason = collusion.not_applicable(self.task, len(self.episodes))
        if reason:
            return {"applicable": False, "reason": reason}
        return transcript_levels.collusion_shown(collusion.run_collusion(self.path))

    def measured(self) -> dict:
        """What the index caches for a run beside its row: CheatBench's verdicts ({judge: {episode: row}},
        verdict_files.cheatbench), the three ladders' measures (the Cheating and Collusion measures read the
        transcripts; coordination.run_coordination) and each agent's judge labels (judge_labels.labels: each label's
        score, excerpt, reason and located place)."""
        return {"cheatbench": verdict_files.cheatbench(self.path), "cheating": self.cheating(),
                "coordination": coordination.run_coordination(self.path), "collusion": self.collusion(),
                "labels": judge_labels.labels(self.path)}

    def levels(self, measured: dict, boards: list[dict] | None = None) -> dict:
        """The three ladders (transcript_levels.compute) from the cached measures and the board."""
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
    {label: {"score": the measure's team score, "agent": the first worker with it, "kind": that agent's kind} or None},
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
        who = next((a for a in agents if is_worker(roles.get(a)) and score(a, label) == top), None)
        team[label] = None if top is None else {"score": top, "agent": who, "kind": kind(who, label)}
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


def cell_of(listed: Listed, rf: RunFiles) -> Cell:
    """The task's stem and the cell from the manifest row, the INDEX.jsonl row, else the run's name (NAME_RE). The
    models come from run.json alone: a role's own model in role_models, else the eval's model, which a role with no
    model of its own runs (src/configuration/swarm_config.py role_models)."""
    rj, meta = rf.run_json, listed.meta
    role_models = rj.get("role_models") or {}
    models = [short_model(role_models.get(role) or rj.get("model")) for role in ROLE_MODELS]
    if listed.source == "manifest":
        return Cell(meta["task"], "solo_cheat" if meta["cell"] == "cheat" else meta["cell"], *models)
    if meta:
        return Cell((meta.get("cell") or "").split(".")[-1].split("_")[0] or None, meta.get("kind"), *models)
    if m := NAME_RE.match(run_name(listed.path)):
        return Cell(m["stem"], m["cell"], *models)
    return Cell(None, None, *models)


def task_label(task: str | None, stem: str | None) -> str:
    """The run's task in the pages' words: its domain (ladder_by_domain.TASK_DOMAIN, the tasks the study reads) and the
    stem of its cell, as "SWE (flipt)"; the task's name for a task with no domain."""
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
    """The influencers in words, "regulator (enforce, claude-haiku-5.5)", or "no influencer"."""
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


def build_row(col: str, listed: Listed, rf: RunFiles, measured: dict) -> dict:
    """The run's index row from its files and its measures (RunFiles.measured's answer)."""
    rj, cfg = rf.run_json, rf.run_json.get("config") or {}
    n_agents = len(rf.episodes) or int(cfg.get("n_agents") or 1)
    cell = cell_of(listed, rf)
    told = worker_conditioning(cell, cfg)
    influencers = influencer_conditioning(cell, cfg, rf.roles)
    sample = (rf.episodes[0].get("episode") if rf.episodes else "") or ""
    agents, workers = agents_words(n_agents, rf.roles)
    return {
        "id": f"{col}/{listed.key}", "col": col, "key": listed.key,
        "run": run_name(listed.path) if listed.source != "manifest" else listed.meta.get("key"),
        "task_name": rf.task, "task_label": task_label(rf.task, cell.stem),
        "instance": sample.split("#")[0] or None,
        "arm_label": f"{agents}; workers {told}; {influencer_words(influencers)}", "agents": agents,
        "worker_conditioning": told, "influencer_conditioning": influencers, "model": cell.worker,
        "model_id": (rj.get("role_models") or {}).get("worker") or rj.get("model"), "n_agents": n_agents,
        "workers": workers, "seed": listed.meta.get("seed", cfg.get("seed")),
        "levels": transcript_levels.index_levels(rf.levels(measured), rf.roles),
        "cheatbench": cheatbench_summary(measured["cheatbench"], rf.episodes, rf.roles),
        "labels": label_summary(measured["labels"], rf.roles),
    }


def measure_run(col: str, listed: Listed) -> dict:
    """One run's cache entry: {"row", "measured", "dir"} (build_row, RunFiles.measured, the run's folder), or
    {"error"} for a run whose files cannot be read."""
    try:
        rf = RunFiles.read(listed.path)
        measured = rf.measured()
        return {"row": build_row(col, listed, rf, measured), "measured": measured, "dir": str(listed.path)}
    except (OSError, ValueError, KeyError, TypeError) as e:
        return {"error": f"{type(e).__name__}: {e}"}


# ---------------------------------------------------------------------------------------------------------- index
def code_stamp() -> str:
    """A hash of the Python files of MEASURING: new code in the measures, the domains' ladders, the judges' readers or
    the viewer measures every run again."""
    digest = hashlib.sha1()
    for path in sorted(p for folder in MEASURING for p in (ROOT / folder).rglob("*.py")):
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def stamp(run_dir: Path) -> list[int]:
    """The run's files as a stamp: their count, their bytes and the latest change, over its whole folder (a new
    verdict file beside the run changes it)."""
    n = size = latest = 0
    for folder, _, files in os.walk(run_dir):
        for name in files:
            st = os.stat(os.path.join(folder, name))
            n, size, latest = n + 1, size + st.st_size, max(latest, st.st_mtime_ns)
    return [n, size, latest]


def valid_runs(runs: Path) -> list[tuple[str, list[Listed]]]:
    """Each collection's valid runs (run_listing's status), the collections without one left out."""
    out = []
    for folder in collections(runs):
        listed = [item for item in list_collection(folder) if item.status == "valid"]
        if listed:
            out.append((folder.name, listed))
    return out


def left_out_task(run_dir: Path) -> str | None:
    """The task of a run the viewer leaves out (ladder_by_domain.hidden_task), read from its run.json; None for a run it
    shows, and for a run.json that does not parse, which measure_run then reports."""
    try:
        task = (read_json(run_dir / "run.json") or {}).get("task")
    except ValueError:
        return None
    return task if hidden_task(task) else None


def measure_all(todo: list[tuple[str, list[int], str, Listed]]) -> dict[str, dict]:
    """The entries of the runs to measure ((run id, stamp, collection, Listed)), measured in WORKERS processes, with a
    line of progress every PROGRESS runs and at the end."""
    entries: dict[str, dict] = {}
    if not todo:
        return entries
    with ProcessPoolExecutor(max_workers=min(WORKERS, len(todo))) as pool:
        done = pool.map(measure_run, [col for _, _, col, _ in todo], [item for *_, item in todo], chunksize=4)
        for n, ((run_id, st, _, _), entry) in enumerate(zip(todo, done), 1):
            entries[run_id] = {**entry, "stamp": st}
            if n % PROGRESS == 0 or n == len(todo):
                print(f"measured {n} of {len(todo)} runs", flush=True)
    return entries


def build_index(runs: Path, cached: dict | None = None) -> dict:
    """Every collection's valid runs, each with its row and its measures, and the runs of the tasks the viewer leaves
    out counted in `left_out`: their entry, {"left_out": task}, is all the index reads of them. A run whose stamp and
    code stamp match `cached` (an earlier index) keeps its entry; the others are measured again (measure_all)."""
    t0 = time.time()
    code = code_stamp()
    old = (cached or {}).get("entries") or {} if (cached or {}).get("code") == code else {}
    cols = valid_runs(runs)
    entries, todo = {}, []
    for name, listed in cols:
        for item in listed:
            run_id, st = f"{name}/{item.key}", stamp(item.path)
            prior = old.get(run_id)
            if prior is not None and prior["stamp"] == st:
                entries[run_id] = prior
            elif task := left_out_task(item.path):
                entries[run_id] = {"left_out": task, "stamp": st}
            else:
                todo.append((run_id, st, name, item))
    entries.update(measure_all(todo))
    rows, errors, collections_out, left_out = [], [], [], 0
    for name, listed in cols:
        label, tag, about = COLLECTION_ABOUT.get(name, (name, "", ""))
        shown = 0
        for item in listed:
            entry = entries[f"{name}/{item.key}"]
            if "error" in entry:
                errors.append({"id": f"{name}/{item.key}", "error": entry["error"]})
            elif "left_out" in entry:
                left_out += 1
            else:
                rows.append({**entry["row"], "interest": interest(entry["row"])})
                shown += 1
        if shown:
            collections_out.append({"key": name, "label": label, "tag": tag, "about": about, "runs": shown})
    rows.sort(key=interest_order)
    return {"version": INDEX_VERSION, "runs": str(runs), "code": code,
            "built_at": time.strftime("%Y-%m-%d %H:%M:%S %Z"), "build_seconds": round(time.time() - t0, 2),
            "measured": len(todo), "collections": collections_out, "errors": errors, "rows": rows,
            "left_out": {"tasks": sorted(HIDDEN_TASKS), "runs": left_out},
            "entries": entries}


def served_index(idx: dict) -> dict:
    """The index as /api/index serves it: its SERVED keys, without the measures, folders and stamps the cache keeps."""
    return {k: idx[k] for k in SERVED}


def load_or_build(runs: Path, rebuild: bool, cache: Path = CACHE) -> dict:
    """The index: the cache's entries where it is of this version and these runs and --rebuild is not given, every
    run whose stamp changed measured again, written back to `cache` when any was."""
    cached = None
    if not rebuild and cache.is_file():
        try:
            cached = read_json(cache)
        except (OSError, ValueError):
            cached = None
        if (cached or {}).get("version") != INDEX_VERSION or (cached or {}).get("runs") != str(runs):
            cached = None
    idx = build_index(runs, cached)
    if idx["measured"] or cached is None or set(idx["entries"]) != set(cached.get("entries") or {}):
        cache.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache.with_suffix(".tmp")
        tmp.write_text(json.dumps(idx, separators=(",", ":")))
        os.replace(tmp, cache)
    return idx
