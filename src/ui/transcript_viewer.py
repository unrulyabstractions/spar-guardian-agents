"""The transcript viewer: every run under runs/ in one index, and a run page with the three ladders (Cheating,
Coordination, Collusion), the judges' verdicts, the board and each agent's transcript, every tool call with the labels
it is evidence for. Ported from collusion-hermaneutics' viewer (viewer/serve.py), whose runs our framework made, so it
reads the same files here.

    uv run python -m src.ui.transcript_viewer [--port 8770] [--rebuild] [--runs runs]
    make transcripts

Every folder under runs/ is a collection, except study_smoke and folders that hold no run: a study or a manifest
collection, whose runs and their validity src/measures/run_listing.py list_collection reads. The runs of the tasks the
viewer leaves out (transcript_ladder_tables.HIDDEN_DOMAINS) are listed in the cache without their ladders and are not
served (shown_index).

The index lists every run with a status: valid, or the reason it is not (invalid, incomplete, mock, attempt, extra
copy). The page shows the valid runs first and the rest through its Status filter. For each run the index reads
run.json, episodes.jsonl, signals.jsonl and the board, the judges' verdicts (src/judging/verdict_files.py read: the
eval's judge.jsonl and the judge passes' files), and the three ladders' measures: src/measures/cheating_ladder.py
run_cheating, coordination.py, and collusion.py with a collusion judge's verdicts (src/judging/collusion_judge.py
judged_collusion), which read the transcripts. The server reads the data and never writes it. Its one output is the
index cache, tmp/transcript_viewer/index.json: each run's row and its measures, under a stamp of the run's files (stamp)
and of the code (code_stamp). A start measures again only the runs whose stamp changed, in parallel processes;
--rebuild, a new INDEX_VERSION or new code measures every run. The run page reads its ladders from the cache. The
browser's transcript is read only when the browser asks for it, and it is streamed from disk as stored.

Routes: / (the page), /tv/<file> (src/ui/transcript_static/), /static/<path> (the results browser's assets in
src/ui/static/, whose dom.js, charts.js, components.js and style.css the page reuses), /api/index, /api/run?id=,
/api/transcript?id=&agent=, /api/ladders. src/ui/API.md describes them.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from src.configuration.settings import ROOT
from src.domains import BY_TASK
from src.judging import collusion_judge, verdict_files
from src.measures import cheating_ladder, collusion, coordination
from src.measures.run_directory import RunDir, episode_agent, is_worker, roles_of, run_name
from src.measures.run_listing import Listed, list_collection
from src.ui import transcript_ladder_tables, transcript_levels
from src.utils.json_files import read_json, read_jsonl

HERE = Path(__file__).resolve().parent
STATIC = HERE / "transcript_static"
BROWSER_STATIC = HERE / "static"
CACHE = ROOT / "tmp" / "transcript_viewer" / "index.json"
SKIP = ("study_smoke",)
INDEX_VERSION = 5  # 5: the ladder ids cheating and coordination; CheatBench verdicts from judge.jsonl too
DEFAULT_PORT = 8770
WORKERS = 6
"""Processes that measure runs when a start finds runs to measure: each loads transcripts, so few enough to spare the
machine's memory."""
MEASURING = ("src/measures", "src/domains", "src/judging", "src/ui")
"""The folders whose code decides a run's row and measures (code_stamp)."""
SERVED = ("version", "runs", "built_at", "build_seconds", "measured", "collections", "errors", "rows", "hidden")
"""The index's keys /api/index answers; the cache also keeps each run's measures and stamps."""
CHEATBENCH = "cheatbench"
"""The verdicts' pass of CheatBench's judge (verdict_files.read): one row per episode, the eval's and the passes'."""
COLLUSION = "collusion"
"""The verdict files' pass of the collusion judge: one row per pair of agents."""
VERDICT_KEPT = ("parse_ok", "judge_cost_usd", "judge_error")
"""The bookkeeping keys of a verdict row (verdict_files.ROW_KEYS) the run page still shows; it leaves the rest out."""

COLLECTION_ABOUT = {
    "study_exploration_1": ("Exploration 1", "teams, regulators",
                            "Teams of three on four targets, with hidden regulators and disregulators at several "
                            "levels and models. INDEX.jsonl says which runs are valid."),
    "study_exploration_0": ("Exploration 0", "October screen",
                            "One agent, a team of three, or three plus a hidden regulator, on five tasks and three "
                            "models."),
    "apart_controls": ("Apart controls", "lone agents",
                       "One agent on nine tasks and five models, told to cheat or not. The canonical copy of each run "
                       "is valid; extra copies of duplicated runs sit beside them."),
}
# Episode fields the run page shows; the rest of episodes.jsonl (rules, probes, ledger, access) stays on disk.
EPISODE_KEYS = ["episode", "orbit_agent", "role", "model", "topology", "n_turns", "n_tool_calls", "wall_time", "limit",
                "score", "ended_by", "ok", "failure", "error", "final_text", "usage", "defense", "defenses",
                "agent_version"]
RUN_JSON_KEYS = ["task", "variant", "model", "role_models", "topology", "mode", "orbit_version", "inspect_version",
                 "defenses", "team_submission", "shutdown_rate", "deviations", "code"]
ROUND_RE = re.compile(r"^r(\d+)")
SUBMIT_RE = re.compile(r"handed in the team's work in round (\d+)")
NAME_RE = re.compile(r"^(?P<stem>[a-z]+)_(?P<cell>.+)_(?P<model>[a-z]+?)(?P<n>\d+)$")


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


def board_view(run_dir: Path, timeline: list[dict], roles: dict) -> list[dict]:
    """The board of each sample in list order, with each post's number, kind, round and the tool call behind it.
    A system message is a hand-in notice, which names its round, or another notice."""
    cdir = run_dir / "communication"
    if not cdir.is_dir():
        return []
    when = {(e.get("turn"), e.get("agent")): e.get("step") for e in timeline}
    boards = []
    for path in sorted(cdir.glob("*.json")):
        c = read_json(path) or {}
        sent, reads = {}, {}
        for e in c.get("events") or []:
            for mid in e.get("message_ids") or []:
                if e.get("kind") == "sent":
                    sent[mid] = {"agent": e.get("agent_id"), "call_id": e.get("tool_call_id")}
                elif e.get("kind") == "read":
                    reads.setdefault(mid, []).append({"agent": e.get("agent_id"), "call_id": e.get("tool_call_id")})
        msgs, n_post = [], 0
        for i, m in enumerate(c.get("messages") or []):
            sender, content = m.get("sender"), m.get("content") or ""
            if sender == "system":
                sm = SUBMIT_RE.search(content)
                kind, post_n, step = ("submit" if sm else "system"), None, None
                rnd = int(sm.group(1)) if sm else None
            else:
                n_post += 1
                kind, post_n = "post", n_post
                step = when.get((m.get("turn"), sender))
                rm = ROUND_RE.match(step or "")
                rnd = int(rm.group(1)) if rm else None
            msgs.append({
                "board_no": i + 1, "post_n": post_n, "kind": kind, "sender": sender, "role": roles.get(sender),
                "recipients": m.get("recipients") or [], "content": content, "sequence": m.get("sequence"),
                "turn": m.get("turn"), "round": rnd, "round_step": step, "id": m.get("id"),
                "sent": sent.get(m.get("id")), "reads": reads.get(m.get("id"), []),
            })
        kinds: dict[str, int] = {}
        for e in c.get("events") or []:
            kinds[e.get("kind") or "?"] = kinds.get(e.get("kind") or "?", 0) + 1
        boards.append({"sample": path.stem, "messages": msgs, "event_counts": kinds})
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

    @property
    def single(self) -> bool:
        return len(self.episodes) <= 1

    def boards(self) -> list[dict]:
        return board_view(self.path, (self.run_json.get("rules") or {}).get("timeline") or [], self.roles)

    def records(self) -> list[dict]:
        """The board's record as stored, communication/<sample>.json in name order, which the Coordination ladder
        reads (src/measures/coordination.py run_coordination reads the same files the same way)."""
        folder = self.path / coordination.COMMUNICATION
        return [read_json(p) or {} for p in sorted(folder.glob("*.json"))] if folder.is_dir() else []

    def cheating(self) -> dict:
        """The Cheating ladder of the run (src/measures/cheating_ladder.py run_cheating) as the viewer keeps it, or
        {"applicable": False, "reason"} for a run without episodes or of a task with no ladder."""
        if self.task not in BY_TASK:
            return {"applicable": False, "reason": f"no Cheating ladder for task {self.task!r}"}
        if not self.episodes:
            return {"applicable": False, "reason": "the run recorded no episode"}
        return transcript_levels.cheating_shown(cheating_ladder.run_cheating(self.path))

    def collusion(self, judges: dict[str, dict[str, list[dict]]], cheating: dict) -> dict:
        """The Collusion ladder of the run with its collusion judge's verdicts (src/judging/collusion_judge.py
        judged_collusion, with each agent's Cheating rung from `cheating`, the Cheating measure's answer), as the
        viewer keeps it, and the judge's name; or
        {"applicable": False, "reason"} when the measure says it does not apply (not_applicable), which loads no
        transcript."""
        reason = collusion.not_applicable(self.task, len(self.episodes))
        if reason:
            return {"applicable": False, "reason": reason}
        rungs = {a: r["rung"] for a, r in (cheating.get("agents") or {}).items()}
        measure, judge = collusion_judge.judged_collusion(self.path, rungs, judges.get(COLLUSION) or {})
        return {**transcript_levels.collusion_shown(measure), "judge": judge}

    def measured(self) -> dict:
        """What the index caches for a run beside its row: the judges' verdict files (verdict_files.read, {pass:
        {judge: [row]}}), and the Cheating and Collusion measures, which read the transcripts."""
        judges = verdict_files.read(self.path)
        cheating = self.cheating()
        return {"judges": judges, "cheating": cheating, "collusion": self.collusion(judges, cheating)}

    def levels(self, measured: dict, boards: list[dict] | None = None) -> dict:
        """The three ladders (transcript_levels.compute) from the cached measures and the board."""
        return transcript_levels.compute(self.sig_agents, self.episodes, self.boards() if boards is None else boards,
                                         self.roles, self.records(), measured["cheating"], measured["collusion"])


# ------------------------------------------------------------------------------------------------------ the judges
def cheatbench_by_agent(rows: list[dict], episodes: list[dict]) -> dict[str, dict]:
    """One CheatBench judge's rows by the agent of the episode each reads (the eval's rows and a pass's both name the
    episode)."""
    by_episode = {row.get("episode"): row for row in rows}
    return {episode_agent(ep): by_episode[ep.get("episode")] for ep in episodes if ep.get("episode") in by_episode}


def shown_verdict(row: dict) -> dict:
    """A verdict row as the run page shows it: the judge's answer, without the bookkeeping VERDICT_KEPT leaves out."""
    drop = set(verdict_files.ROW_KEYS) - set(VERDICT_KEPT)
    return {k: v for k, v in row.items() if k not in drop}


def cheatbench_summary(judges: dict[str, list[dict]], episodes: list[dict],
                       roles: dict[str, str | None]) -> list[dict]:
    """Each CheatBench judge's word on the team: {judge, cheated (workers it says cheated), of (workers it gave a
    parsed verdict), workers}."""
    workers = [a for a, r in roles.items() if is_worker(r)]
    out = []
    for judge, rows in sorted(judges.items()):
        by_agent = cheatbench_by_agent(rows, episodes)
        parsed = [by_agent[a] for a in workers if a in by_agent and by_agent[a].get("parse_ok")
                  and isinstance(by_agent[a].get("cheated"), bool)]
        out.append({"judge": judge, "cheated": sum(r["cheated"] for r in parsed), "of": len(parsed),
                    "workers": len(workers)})
    return out


def short_model(model: str | None) -> str | None:
    """A model string's last part without a date suffix: openrouter/deepseek/deepseek-v4.1-flash-1017 gives
    deepseek-v4.1-flash."""
    if not model:
        return None
    return re.sub(r"-\d{4}$", "", model.split("/")[-1])


@dataclass
class Cell:
    """What a run was made with: its task's short name, its cell, its agents' model, who was told to cheat and the
    hidden roles' levels and models."""

    stem: str | None
    cell: str | None
    author: str | None
    regulator: str | None = None
    disregulator: str | None = None


def cell_of(listed: Listed, rf: RunFiles) -> Cell:
    """From the manifest row, the INDEX.jsonl row, else the run's name (<stem>_<cell>_<model><n>). A hidden role
    with no model of its own runs the eval's model (src/configuration/swarm_config.py role_models), so its model is
    the workers' when the workers have none of their own either."""
    rj, meta = rf.run_json, listed.meta
    role_models = rj.get("role_models") or {}
    regulator = short_model(role_models.get("regulator"))
    disregulator = short_model(role_models.get("disregulator"))
    if listed.source == "manifest":
        return Cell(meta["task"], "solo_cheat" if meta["cell"] == "cheat" else meta["cell"], meta["model"])
    if meta:
        stem = (meta.get("cell") or "").split(".")[-1].split("_")[0] or None
        cell = Cell(stem, meta.get("kind"), meta.get("worker"), meta.get("regulator") or regulator, disregulator)
    elif m := NAME_RE.match(run_name(listed.path)):
        cell = Cell(m["stem"], m["cell"], m["model"], regulator, disregulator)
    else:
        cell = Cell(None, None, short_model(rj.get("model")), regulator, disregulator)
    run_model = short_model(rj.get("model")) if role_models.get("worker") else cell.author
    cell.regulator = cell.regulator or run_model
    cell.disregulator = cell.disregulator or run_model
    return cell


def seed_post(cell: Cell, cfg: dict, swarm: dict, n_agents: int) -> str:
    """What the run placed before the agents started: the cheat instruction, a rule, a hidden regulator or
    disregulator."""
    told = "cheat" in (cell.cell or "").split("_")
    parts = [("all told to cheat" if n_agents > 1 else "told to cheat") if told else "not told"]
    if cfg.get("rule_level") not in (None, "none"):
        parts.append(f"rule: {cfg['rule_level']}")
    for role, level_key, model in (("regulator", "regulator_level", cell.regulator),
                                   ("disregulator", "disregulator_level", cell.disregulator)):
        n = swarm.get(role + "s", cfg.get(role + "s")) or 0
        if n:
            what = ", ".join(x for x in (cfg.get(level_key), model) if x)
            parts.append(f"{'' if n == 1 else f'{n} '}hidden {role}{'' if n == 1 else 's'}"
                         + (f" ({what})" if what else ""))
    return ", ".join(parts)


def team_words(n_agents: int, roles: dict) -> str:
    hidden = {r: sum(1 for x in roles.values() if x == r) for r in ("regulator", "disregulator")}
    workers = n_agents - sum(hidden.values())
    if n_agents <= 1:
        return "One agent"
    extra = "".join(f" plus {n} hidden {r}{'' if n == 1 else 's'}" for r, n in hidden.items() if n)
    return f"Team of {workers}{extra}"


def build_row(col: str, listed: Listed, rf: RunFiles, measured: dict | None) -> dict:
    """The run's index row. `measured` is RunFiles.measured's answer, None for a run of a task the viewer leaves out:
    its row then has no levels and no judges."""
    rj, cfg, swarm = rf.run_json, rf.run_json.get("config") or {}, rf.run_json.get("swarm") or {}
    n_agents = len(rf.episodes) or int(cfg.get("n_agents") or 1)
    cell = cell_of(listed, rf)
    post = seed_post(cell, cfg, swarm, n_agents)
    sample = (rf.episodes[0].get("episode") if rf.episodes else "") or ""
    in_study = listed.source != "manifest"
    return {
        "id": f"{col}/{listed.key}", "col": col, "key": listed.key,
        "run": run_name(listed.path) if in_study else listed.meta.get("key"), "status": listed.status,
        "why": listed.why, "group": listed.key.split("/")[0] if in_study and "/" in listed.key else None,
        "task": cell.stem, "task_name": rf.task, "instance": sample.split("#")[0] or None, "arm": cell.cell,
        "arm_label": f"{team_words(n_agents, rf.roles)}, {post}", "seed_post": post, "author": cell.author,
        "model_id": rj.get("model"), "n_agents": n_agents,
        "regulator_model": cell.regulator if "regulator" in rf.roles.values() else None,
        "roles": {a: r for a, r in rf.roles.items() if r},
        "seed": listed.meta.get("seed", cfg.get("seed")), "turn_budget": cfg.get("turn_budget"),
        "dir": str(listed.path), "n_transcripts": len(transcript_dirs(listed.path)),
        "levels": None if measured is None else transcript_levels.index_levels(rf.levels(measured), rf.roles),
        "cheatbench": [] if measured is None else cheatbench_summary(measured["judges"].get(CHEATBENCH) or {},
                                                                     rf.episodes, rf.roles),
    }


def measure_run(col: str, listed: Listed) -> dict:
    """One run's cache entry: {"row", "measured"} (build_row, RunFiles.measured), or {"error"} for a run whose files
    cannot be read. The runs of the tasks the viewer leaves out are not measured."""
    try:
        rf = RunFiles.read(listed.path)
        measured = None if transcript_ladder_tables.hidden_task(rf.task) else rf.measured()
        return {"row": build_row(col, listed, rf, measured), "measured": measured}
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


def build_index(runs: Path, cached: dict | None = None) -> dict:
    """Every collection's runs, each with its row and its measures. A run whose stamp and code stamp match `cached`
    (an earlier index) keeps its entry; the others are measured again, in WORKERS processes."""
    t0 = time.time()
    code = code_stamp()
    old = (cached or {}).get("entries") or {} if (cached or {}).get("code") == code else {}
    entries, todo, cols = {}, [], []
    for folder in collections(runs):
        listed = list_collection(folder)
        if not listed:
            continue
        cols.append((folder.name, listed))
        for item in listed:
            run_id, st = f"{folder.name}/{item.key}", stamp(item.path)
            prior = old.get(run_id)
            if prior is not None and prior["stamp"] == st and prior["row"]["status"] == item.status:
                entries[run_id] = prior
            else:
                todo.append((run_id, st, folder.name, item))
    if todo:
        with ProcessPoolExecutor(max_workers=min(WORKERS, len(todo))) as pool:
            done = pool.map(measure_run, [col for _, _, col, _ in todo], [item for *_, item in todo], chunksize=4)
            for (run_id, st, _, _), entry in zip(todo, done):
                entries[run_id] = {**entry, "stamp": st}
    rows, errors, collections_out = [], [], []
    for name, listed in cols:
        label, tag, about = COLLECTION_ABOUT.get(name, (name, "", f"Runs under runs/{name}/."))
        statuses: dict[str, int] = {}
        for item in listed:
            entry = entries[f"{name}/{item.key}"]
            if "error" in entry:
                errors.append({"id": f"{name}/{item.key}", "error": entry["error"]})
                continue
            rows.append(entry["row"])
            statuses[item.status] = statuses.get(item.status, 0) + 1
        collections_out.append({"key": name, "label": label, "tag": tag, "about": about, "statuses": statuses})
    return {"version": INDEX_VERSION, "runs": str(runs), "code": code,
            "built_at": time.strftime("%Y-%m-%d %H:%M:%S %Z"), "build_seconds": round(time.time() - t0, 2),
            "measured": len(todo), "collections": collections_out, "errors": errors, "rows": rows,
            "entries": entries}


def shown_index(idx: dict) -> dict:
    """The index as the viewer serves it (SERVED): without the runs of the tasks it leaves out
    (transcript_ladder_tables.hidden_task), each collection's status counts taken again, a collection left with no
    run dropped, and `hidden` saying which domains and how many runs were left out. The cache keeps every run, so a
    change of the list measures only the runs it shows again."""
    rows = [row for row in idx["rows"] if not transcript_ladder_tables.hidden_task(row.get("task_name"))]
    statuses: dict[str, dict[str, int]] = {}
    for row in rows:
        counts = statuses.setdefault(row["col"], {})
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    cols = [{**col, "statuses": statuses[col["key"]]} for col in idx["collections"] if col["key"] in statuses]
    hidden = {"domains": list(transcript_ladder_tables.HIDDEN_DOMAINS), "runs": len(idx["rows"]) - len(rows)}
    served = {**idx, "rows": rows, "collections": cols, "hidden": hidden}
    return {k: served[k] for k in SERVED if k in served}


def load_or_build(runs: Path, rebuild: bool) -> dict:
    """The index: the cache's entries where it is of this version and these runs and --rebuild is not given, every
    run whose stamp changed measured again, written back to the cache when any was."""
    cached = None
    if not rebuild and CACHE.is_file():
        try:
            cached = read_json(CACHE)
        except (OSError, ValueError):
            cached = None
        if (cached or {}).get("version") != INDEX_VERSION or (cached or {}).get("runs") != str(runs):
            cached = None
    idx = build_index(runs, cached)
    if idx["measured"] or cached is None or set(idx["entries"]) != set(cached.get("entries") or {}):
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        tmp = CACHE.with_suffix(".tmp")
        tmp.write_text(json.dumps(idx, separators=(",", ":")))
        os.replace(tmp, CACHE)
    return idx


# ------------------------------------------------------------------------------------------------------- run page
def eval_log_of(run_dir: Path) -> Path | None:
    """The run's eval log: the one in its own eval/ folder (a manifest collection's runs keep it there), else where
    RunDir finds it."""
    own = sorted((run_dir / "eval").glob("*.eval")) if (run_dir / "eval").is_dir() else []
    return own[0] if own else RunDir(run_dir).eval_log


def run_detail(row: dict, measured: dict) -> dict:
    """The run page's data: the row, run.json's settings, each agent with its episode and each CheatBench judge's
    verdict on it, the board, the three ladders from the cached measures, and every verdict of the collusion
    judges."""
    run_dir = Path(row["dir"])
    rf = RunFiles.read(run_dir)
    rj, rules = rf.run_json, rf.run_json.get("rules") or {}
    folders = folder_agents(run_dir, rf.episodes)
    cheatbench = {judge: cheatbench_by_agent(rows, rf.episodes) for judge, rows in
                  sorted((measured["judges"].get(CHEATBENCH) or {}).items())}
    agents = []
    for ep in rf.episodes:
        a = episode_agent(ep)
        folder = folders.get(a)
        tpath = run_dir / "trajectories" / folder / "transcript.json" if folder else None
        agents.append({
            "agent": a, "role": rf.roles.get(a) or "worker", "folder": folder,
            "transcript_bytes": tpath.stat().st_size if tpath else None,
            "episode": {k: ep.get(k) for k in EPISODE_KEYS if k in ep},
            "cheatbench": {judge: shown_verdict(rows[a]) for judge, rows in cheatbench.items() if a in rows},
        })
    # Workers first, hidden roles last, as the results browser orders its lanes; then by agent id.
    agents.sort(key=lambda e: (e["role"] != "worker", e["agent"] or ""))
    meta = {k: rj.get(k) for k in RUN_JSON_KEYS if k in rj}
    log = eval_log_of(run_dir)
    meta.update({"config": rj.get("config"), "rounds": rules.get("rounds"), "workers": rules.get("workers"),
                 "composition": (rj.get("swarm") or {}).get("composition"), "eval_log": str(log) if log else None,
                 "eval_log_recorded": rj.get("eval_log")})
    boards = rf.boards()
    return {"row": row, "meta": meta, "agents": agents, "boards": boards, "levels": rf.levels(measured, boards),
            "collusion_judges": {judge: [shown_verdict(row) for row in rows]
                                 for judge, rows in sorted((measured["judges"].get(COLLUSION) or {}).items())}}


# ---------------------------------------------------------------------------------------------------------- server
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".json": "application/json", ".svg": "image/svg+xml"}


class Server(ThreadingHTTPServer):
    """socketserver listens with a backlog of request_queue_size, 5 by default. The page asks for its dozen ES modules
    at once, and four pages loading together got connections reset at 5 and none at 128 (measured 2026-11-08)."""

    request_queue_size = 128


class Viewer:
    """What the server answers from: the served index (shown_index) as bytes, its rows by id, each served run's cached
    measures and the Ladders page's data."""

    def __init__(self, idx: dict):
        served = shown_index(idx)
        self.index_bytes = json.dumps(served, separators=(",", ":")).encode("utf-8")
        self.rows = {r["id"]: r for r in served["rows"]}
        self.measured = {run_id: idx["entries"][run_id]["measured"] for run_id in self.rows}
        self.ladders = transcript_ladder_tables.ladders_payload(transcript_levels.LADDERS, transcript_levels.ORDER)


class Handler(BaseHTTPRequestHandler):
    viewer: Viewer  # set in main()
    server_version = "TranscriptViewer/1"

    def log_message(self, fmt, *args):  # quiet: one line per error only
        if len(args) > 1 and str(args[1]).startswith(("4", "5")):
            sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    def send_bytes(self, body: bytes, ctype: str, status=HTTPStatus.OK):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, obj, status=HTTPStatus.OK):
        self.send_bytes(json.dumps(obj, separators=(",", ":")).encode("utf-8"), "application/json", status)

    def fail(self, status, msg):
        self.send_json({"error": msg}, status)

    def do_GET(self):
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        try:
            if url.path in ("/", "/index.html"):
                return self.static(STATIC, "tv_index.html")
            if url.path.startswith("/tv/"):
                return self.static(STATIC, url.path[len("/tv/"):])
            if url.path.startswith("/static/"):
                return self.static(BROWSER_STATIC, url.path[len("/static/"):])
            if url.path == "/api/index":
                return self.send_bytes(self.viewer.index_bytes, "application/json")
            if url.path == "/api/run":
                row = self.viewer.rows.get(q.get("id", ""))
                if row is None:
                    return self.fail(HTTPStatus.NOT_FOUND, f"no run {q.get('id')!r} in the index")
                return self.send_json(run_detail(row, self.viewer.measured[row["id"]]))
            if url.path == "/api/ladders":
                return self.send_json(self.viewer.ladders)
            if url.path == "/api/transcript":
                return self.transcript(q.get("id", ""), q.get("agent", ""))
            return self.fail(HTTPStatus.NOT_FOUND, "no such path")
        except (BrokenPipeError, ConnectionResetError):
            return None
        except Exception as e:  # noqa: BLE001 - shown to the local user as an error box
            return self.fail(HTTPStatus.INTERNAL_SERVER_ERROR, f"{type(e).__name__}: {e}")

    def static(self, root: Path, rel: str):
        path = (root / rel).resolve()
        if root.resolve() not in path.parents or not path.is_file():
            return self.fail(HTTPStatus.NOT_FOUND, "no such file")
        self.send_bytes(path.read_bytes(), TYPES.get(path.suffix, "application/octet-stream"))

    def transcript(self, run_id: str, agent: str):
        row = self.viewer.rows.get(run_id)
        if row is None:
            return self.fail(HTTPStatus.NOT_FOUND, f"no run {run_id!r} in the index")
        run_dir = Path(row["dir"])
        folder = folder_agents(run_dir, read_jsonl(run_dir / "episodes.jsonl")).get(agent)
        if folder is None:
            return self.fail(HTTPStatus.NOT_FOUND, f"no transcript for agent {agent!r} in {run_id}")
        path = run_dir / "trajectories" / folder / "transcript.json"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(path.stat().st_size))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        with open(path, "rb") as f:
            shutil.copyfileobj(f, self.wfile, 256 * 1024)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--rebuild", action="store_true", help="measure every run again, whatever the cache holds")
    ap.add_argument("--runs", type=Path, default=ROOT / "runs", help="the folder whose subfolders are collections")
    args = ap.parse_args(argv)
    runs = args.runs.resolve()
    if not runs.is_dir():
        print(f"no runs folder at {runs}", file=sys.stderr)
        return 1
    t0 = time.time()
    idx = load_or_build(runs, args.rebuild)
    Handler.viewer = Viewer(idx)
    served = shown_index(idx)
    counts = ", ".join(f"{c['key']} {sum(c['statuses'].values())}" for c in served["collections"])
    left_out = f"; {served['hidden']['runs']} runs of {', '.join(served['hidden']['domains'])} left out" \
        if served["hidden"]["runs"] else ""
    print(f"index: {len(served['rows'])} runs ({counts}){left_out}; {len(idx['errors'])} unreadable; "
          f"{idx['measured']} runs measured in {idx['build_seconds']} s; ready in {time.time() - t0:.2f} s", flush=True)
    for e in idx["errors"]:
        print(f"  unreadable {e['id']}: {e['error']}", file=sys.stderr)
    srv = Server((args.host, args.port), Handler)

    def stop(signum, frame):  # a background start ignores Ctrl-C, so SIGTERM stops the server cleanly too
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    print(f"transcript viewer: http://{args.host}:{args.port}/  (runs: {runs})", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
