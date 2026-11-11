"""The transcript viewer: every run under runs/ in one index, and a run page with the six ladders, the board and each
agent's transcript, every sentence coloured by a lens. Ported from collusion-hermaneutics' viewer (viewer/serve.py),
whose runs our framework made, so it reads the same files here.

    uv run python -m src.ui.transcript_viewer [--port 8770] [--rebuild] [--runs runs]
    make transcripts

Every folder under runs/ is a collection, except study_smoke and folders that hold no run:
- a study (study_exploration_0, study_exploration_1) keeps its runs at its top level and in its groups
  (src/measures/run_directory.py study_runs). Where the study has an INDEX.jsonl, its valid flag says which runs are
  valid. Without one, a run at the top level is valid and a run in a group carries the group's name.
- a manifest collection (apart_controls) lists its runs in manifest.jsonl: the canonical copy of each at
  <model>/<task>/<cell>/s<seed>/, the extra copies at extra_duplicate_samples/<model>/<task>/<cell>/s<seed>__<host>/.

The index lists every run with a status: valid, or the reason it is not (invalid, incomplete, mock, attempt, extra
copy). The page shows the valid runs first and the rest through its Status filter. The index reads each run's run.json,
episodes.jsonl, signals.jsonl and board, and the judged ladder (ladder.jsonl) and CheatBench's judge (judge.jsonl)
where the run was judged; it opens no transcript. The server reads the data and never writes it. Its one output is the
index cache, tmp/transcript_viewer/index.json, built on the first start and again with --rebuild. A transcript is read
only when the browser asks for it, and it is streamed from disk as stored.

Routes: / (the page), /tv/<file> (src/ui/transcript_static/), /static/<path> (the results browser's assets in
src/ui/static/, whose dom.js, charts.js, components.js and style.css the page reuses), /api/index, /api/run?id=,
/api/transcript?id=&agent=, /api/lenses, /api/lens?name=, /api/ladders. src/ui/API.md describes them.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import sys
import time
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from src.configuration.settings import ROOT
from src.measures.run_directory import GROUPS, RunDir, run_name, study_runs
from src.measures.rungs import RUNGS
from src.ui import transcript_ladder_tables, transcript_levels
from src.ui.transcript_levels import episode_agent, is_worker
from src.utils.json_files import read_json, read_jsonl

HERE = Path(__file__).resolve().parent
STATIC = HERE / "transcript_static"
BROWSER_STATIC = HERE / "static"
LENSES = HERE / "lenses"
CACHE = ROOT / "tmp" / "transcript_viewer" / "index.json"
SKIP = ("study_smoke",)
INDEX_VERSION = 1
DEFAULT_PORT = 8770

COLLECTION_ABOUT = {
    "study_exploration_1": ("Exploration 1", "teams, regulators",
                            "Teams of three on four targets, with hidden regulators and disregulators at several "
                            "levels and models. Code ladder only, no judge. INDEX.jsonl says which runs are valid."),
    "study_exploration_0": ("Exploration 0", "October screen",
                            "One agent, a team of three, or three plus a hidden regulator, on five tasks and three "
                            "models. Judged by the judged ladder and by CheatBench's rules, both GPT-6 Luna."),
    "apart_controls": ("Apart controls", "lone agents",
                       "One agent on nine tasks and five models, told to cheat or not. Code ladder only. The "
                       "canonical copy of each run is valid; extra copies of duplicated runs sit beside them."),
}
GROUP_STATUS = {"attempts": "attempt", "set_aside": "set aside"}
EXTRA = "extra_duplicate_samples"

# Episode fields the run page shows; the rest of episodes.jsonl (rules, probes, ledger, access) stays on disk.
EPISODE_KEYS = ["episode", "orbit_agent", "role", "model", "topology", "n_turns", "n_tool_calls", "wall_time", "limit",
                "score", "ended_by", "ok", "failure", "error", "final_text", "usage", "defense", "defenses",
                "agent_version"]
# Bookkeeping keys of judge.jsonl and ladder.jsonl rows that the run page leaves out.
JUDGE_DROP = {"judge_schema", "judge_usage", "judge_prompt", "transcript_path", "judge_generation_config",
              "judge_generation_config_unsupported", "id", "replicate", "row", "output_mode", "judge_attempts",
              "raw"}
RUN_JSON_KEYS = ["task", "variant", "model", "role_models", "topology", "mode", "orbit_version", "inspect_version",
                 "defenses", "team_submission", "shutdown_rate", "deviations", "code"]
ROUND_RE = re.compile(r"^r(\d+)")
SUBMIT_RE = re.compile(r"handed in the team's work in round (\d+)")
NAME_RE = re.compile(r"^(?P<stem>[a-z]+)_(?P<cell>.+)_(?P<model>[a-z]+?)(?P<n>\d+)$")


# ------------------------------------------------------------------------------------------------- the collections
@dataclass
class Listed:
    """One run of a collection: its key (its path under the collection, which the run's id carries), its folder, its
    status (valid, or why not) and the INDEX.jsonl or manifest.jsonl row that names it."""

    key: str
    path: Path
    status: str
    why: str = ""
    meta: dict = field(default_factory=dict)
    source: str = "study"


def collections(runs: Path) -> list[Path]:
    """The collection folders under runs/: every folder but SKIP, the known ones first."""
    folders = [p for p in runs.iterdir() if p.is_dir() and p.name not in SKIP and not p.name.startswith(".")]
    known = list(COLLECTION_ABOUT)
    return sorted(folders, key=lambda p: (known.index(p.name) if p.name in known else len(known), p.name))


def manifest_runs(folder: Path) -> list[Listed]:
    """A manifest collection's runs: the canonical copy is valid, an extra copy is an extra copy."""
    out = []
    for row in read_jsonl(folder / "manifest.jsonl"):
        rel = Path(row["model"], row["task"], row["cell"])
        if row.get("canonical"):
            path, status = folder / rel / f"s{row['seed']}", "valid" if row.get("valid") else "invalid"
        else:
            path, status = folder / EXTRA / rel / f"s{row['seed']}__{row['host']}", "extra copy"
        why = "" if row.get("canonical") else f"one of {row.get('n_valid_copies')} valid copies of {row['run_name']}"
        out.append(Listed(path.relative_to(folder).as_posix(), path, status, why, row, "manifest"))
    return out


def study_listing(folder: Path) -> list[Listed]:
    """A study's runs, with INDEX.jsonl's verdict where the study has one, else the group's name."""
    index = {row["path"]: row for row in read_jsonl(folder / "INDEX.jsonl")}
    out = []
    for path in study_runs(folder):
        key = path.relative_to(folder).as_posix()
        group = path.parent.name if path.parent != folder and path.parent.name in GROUPS else None
        group_status = GROUP_STATUS.get(group, group)
        meta = index.get(key)
        if index and meta is None:
            status, why = "not indexed", "no INDEX.jsonl row names this run"
        elif meta is not None and meta.get("valid") is not None:
            status = "valid" if meta["valid"] else (group_status or "invalid")
            why = "; ".join(meta.get("why") or []) or meta.get("reason") or ""
        elif meta is not None:  # INDEX.jsonl names the run and gives no verdict: the group decides
            status, why = ("valid", "INDEX.jsonl gives no verdict; the run sits at the study's top level") \
                if group is None else (group_status, "; ".join(meta.get("why") or []) or f"in the study's {group}/")
        else:
            status, why = ("valid", "") if group is None else (group_status, f"in the study's {group}/ folder")
        out.append(Listed(key, path, status, why, meta or {}))
    return out


def list_collection(folder: Path) -> list[Listed]:
    runs = manifest_runs(folder) if (folder / "manifest.jsonl").is_file() else study_listing(folder)
    return [r for r in runs if (r.path / "run.json").is_file()]


# ----------------------------------------------------------------------------------------------------- one run's data
def run_roles(rj: dict, episodes: list[dict]) -> dict[str, str | None]:
    """agent -> role (worker, regulator, disregulator), None in a run with no roles."""
    roles: dict[str, str | None] = {episode_agent(ep): None for ep in episodes}
    roles.update((rj.get("swarm") or {}).get("roles") or {})
    roles.update({episode_agent(ep): ep["role"] for ep in episodes if ep.get("role")})
    return roles


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
    team: dict | None
    sig_agents: dict[str, dict]
    roles: dict[str, str | None]

    @classmethod
    def read(cls, path: Path) -> RunFiles:
        rj = read_json(path / "run.json") or {}
        episodes = read_jsonl(path / "episodes.jsonl")
        signals = read_jsonl(path / "signals.jsonl")
        team = next((s for s in signals if s.get("row") == "team"), None) or (rj.get("signals") or {}).get("team")
        sig_agents = {s.get("agent"): s for s in signals if s.get("row") == "agent"}
        return cls(path, rj, episodes, team, sig_agents, run_roles(rj, episodes))

    @property
    def single(self) -> bool:
        return len(self.episodes) <= 1

    def boards(self) -> list[dict]:
        return board_view(self.path, (self.run_json.get("rules") or {}).get("timeline") or [], self.roles)

    def levels(self, boards: list[dict] | None = None) -> dict:
        return transcript_levels.compute(self.team, self.sig_agents, self.episodes,
                                         self.boards() if boards is None else boards, self.roles)


def judge_rows(run_dir: Path, name: str, episodes: list[dict]) -> dict[str, dict]:
    """A judge file's agent rows by agent (judge.jsonl rows carry their episode alone) and its team row as "_team",
    without the bookkeeping keys."""
    by_episode = {ep.get("episode"): episode_agent(ep) for ep in episodes}
    out = {}
    for r in read_jsonl(run_dir / name):
        row = {k: v for k, v in r.items() if k not in JUDGE_DROP}
        if r.get("row") == "team":
            out["_team"] = row
            continue
        ep = r.get("episode") or ""
        agent = r.get("agent") or by_episode.get(ep) or (ep.rsplit("@", 1)[1] if "@" in ep else None)
        if agent is None and len(episodes) == 1:
            agent = episode_agent(episodes[0])
        out[agent] = row
    return out


def judge_summary(rows: dict[str, dict], roles: dict, with_rung: bool) -> dict | None:
    """The team's verdict of one judge: cheated when any worker's parsed verdict says so; the highest judged rung of
    the workers for the judged ladder."""
    workers = [r for a, r in rows.items() if a != "_team" and is_worker(roles.get(a))]
    if not workers:
        return None
    parsed = [r for r in workers if r.get("parse_ok") and r.get("cheated") is not None]
    out = {"yes": any(r["cheated"] is True for r in parsed) if parsed else None,
           "n": sum(r.get("cheated") is True for r in parsed), "of": len(workers),
           "judge": next((r.get("judge") for r in workers if r.get("judge")), None)}
    if with_rung:
        judged = [r["rung"] for r in parsed if r.get("rung") in RUNGS]
        out["rung"] = max(judged, key=RUNGS.index) if judged else None
    return out


def agree_status(label: str | None, judges: dict) -> tuple[str, list[str]]:
    """Whether the code ladder's cheat label and each judge's cheating verdict agree."""
    if not judges:
        return "no judge", []
    verdicts = {k: v.get("yes") for k, v in judges.items() if v.get("yes") is not None}
    if not verdicts:
        return "no answer", []
    if label not in ("cheat", "no cheat"):
        return "not comparable", []
    disagree = [k for k, yes in verdicts.items() if yes != (label == "cheat")]
    return ("disagree" if disagree else "agree"), disagree


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


def build_row(col: str, listed: Listed) -> dict:
    rf = RunFiles.read(listed.path)
    rj, cfg, swarm = rf.run_json, rf.run_json.get("config") or {}, rf.run_json.get("swarm") or {}
    team = rf.team or {}
    task = rj.get("task") or team.get("task")
    n_agents = len(rf.episodes) or int(cfg.get("n_agents") or 1)
    rung = transcript_levels.team_rung(team, rf.roles)
    label = transcript_levels.cheat_label(rung, task)
    judges = {}
    for key, name, with_rung in (("ladder", "ladder.jsonl", True), ("cheatbench", "judge.jsonl", False)):
        summary = judge_summary(judge_rows(listed.path, name, rf.episodes), rf.roles, with_rung)
        if summary is not None:
            judges[key] = summary
    agree, disagree = agree_status(label, judges)
    cell = cell_of(listed, rf)
    post = seed_post(cell, cfg, swarm, n_agents)
    sample = team.get("sample") or (rf.episodes[0].get("episode") if rf.episodes else "") or ""
    if rf.single:
        levels = transcript_levels.single_levels(rung)
    else:
        levels = transcript_levels.index_levels(rf.levels(), rf.roles)
    in_study = listed.source != "manifest"
    return {
        "id": f"{col}/{listed.key}", "col": col, "key": listed.key,
        "run": run_name(listed.path) if in_study else listed.meta.get("key"), "status": listed.status,
        "why": listed.why, "group": listed.key.split("/")[0] if in_study and "/" in listed.key else None,
        "task": cell.stem, "task_name": task, "instance": sample.split("#")[0] or None, "arm": cell.cell,
        "arm_label": f"{team_words(n_agents, rf.roles)}, {post}", "seed_post": post, "author": cell.author,
        "model_id": rj.get("model"), "n_agents": n_agents,
        "regulator_model": cell.regulator if "regulator" in rf.roles.values() else None,
        "roles": {a: r for a, r in rf.roles.items() if r}, "rung": rung,
        "rung_code": team.get("rung_by_code") if team.get("raised_by_proof") else None,
        "floor": transcript_levels.floor_of(task), "label": label,
        "collab": None if rf.single else team.get("collaboration"), "judges": judges, "agree": agree,
        "disagree": disagree, "presentation": team.get("presentation"),
        "seed": listed.meta.get("seed", cfg.get("seed")), "turn_budget": cfg.get("turn_budget"),
        "dir": str(listed.path), "n_transcripts": len(transcript_dirs(listed.path)), "levels": levels,
    }


# ---------------------------------------------------------------------------------------------------------- index
def build_index(runs: Path) -> dict:
    t0 = time.time()
    rows, cols, errors = [], [], []
    for folder in collections(runs):
        listed = list_collection(folder)
        if not listed:
            continue
        label, tag, about = COLLECTION_ABOUT.get(folder.name, (folder.name, "", f"Runs under runs/{folder.name}/."))
        statuses: dict[str, int] = {}
        for item in listed:
            try:
                rows.append(build_row(folder.name, item))
            except (OSError, ValueError, KeyError, TypeError) as e:
                errors.append({"id": f"{folder.name}/{item.key}", "error": f"{type(e).__name__}: {e}"})
                continue
            statuses[item.status] = statuses.get(item.status, 0) + 1
        cols.append({"key": folder.name, "label": label, "tag": tag, "about": about, "statuses": statuses})
    return {"version": INDEX_VERSION, "runs": str(runs), "built_at": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
            "build_seconds": round(time.time() - t0, 2), "collections": cols, "errors": errors, "rows": rows}


def load_or_build(runs: Path, rebuild: bool) -> dict:
    """The cached index when it is of this version and these runs, else a fresh build, written to the cache."""
    if not rebuild and CACHE.is_file():
        try:
            idx = read_json(CACHE)
            if idx.get("version") == INDEX_VERSION and idx.get("runs") == str(runs):
                return idx
        except (OSError, ValueError):
            pass
    idx = build_index(runs)
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


def run_detail(row: dict) -> dict:
    run_dir = Path(row["dir"])
    rf = RunFiles.read(run_dir)
    rj, rules = rf.run_json, rf.run_json.get("rules") or {}
    folders = folder_agents(run_dir, rf.episodes)
    ladder = judge_rows(run_dir, "ladder.jsonl", rf.episodes)
    cheatbench = judge_rows(run_dir, "judge.jsonl", rf.episodes)
    agents = []
    for ep in rf.episodes:
        a = episode_agent(ep)
        folder = folders.get(a)
        tpath = run_dir / "trajectories" / folder / "transcript.json" if folder else None
        agents.append({
            "agent": a, "role": rf.roles.get(a) or "worker", "folder": folder,
            "transcript_bytes": tpath.stat().st_size if tpath else None,
            "episode": {k: ep.get(k) for k in EPISODE_KEYS if k in ep},
            "signals": rf.sig_agents.get(a), "ladder_judge": ladder.get(a), "cheatbench_judge": cheatbench.get(a),
        })
    # Workers first, hidden roles last, as the results browser orders its lanes; then by agent id.
    agents.sort(key=lambda e: (e["role"] != "worker", e["agent"] or ""))
    meta = {k: rj.get(k) for k in RUN_JSON_KEYS if k in rj}
    log = eval_log_of(run_dir)
    meta.update({"config": rj.get("config"), "rounds": rules.get("rounds"), "workers": rules.get("workers"),
                 "composition": (rj.get("swarm") or {}).get("composition"), "eval_log": str(log) if log else None,
                 "eval_log_recorded": rj.get("eval_log")})
    boards = rf.boards()
    return {"row": row, "meta": meta, "team": rf.team, "agents": agents, "boards": boards,
            "levels": rf.levels(boards), "ladder_team": ladder.get("_team")}


# ---------------------------------------------------------------------------------------------------------- server
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".json": "application/json", ".svg": "image/svg+xml"}
LENS_NAME = re.compile(r"[A-Za-z0-9_.-]+")


class Server(ThreadingHTTPServer):
    """socketserver listens with a backlog of request_queue_size, 5 by default. The page asks for its dozen ES modules
    at once, and four pages loading together got connections reset at 5 and none at 128 (measured 2026-11-08)."""

    request_queue_size = 128


class Viewer:
    def __init__(self, idx: dict):
        self.index_bytes = json.dumps(idx, separators=(",", ":")).encode("utf-8")
        self.rows = {r["id"]: r for r in idx["rows"]}
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
                return self.send_json(run_detail(row))
            if url.path == "/api/lenses":
                names = sorted(p.stem for p in LENSES.glob("*.json")) if LENSES.is_dir() else []
                return self.send_json({"lenses": names})
            if url.path == "/api/lens":
                name = q.get("name", "")
                if not LENS_NAME.fullmatch(name) or not (LENSES / f"{name}.json").is_file():
                    return self.fail(HTTPStatus.NOT_FOUND, f"no lens {name!r}")
                return self.send_bytes((LENSES / f"{name}.json").read_bytes(), "application/json")
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
    ap.add_argument("--rebuild", action="store_true", help="rebuild tmp/transcript_viewer/index.json from the runs")
    ap.add_argument("--runs", type=Path, default=ROOT / "runs", help="the folder whose subfolders are collections")
    args = ap.parse_args(argv)
    runs = args.runs.resolve()
    if not runs.is_dir():
        print(f"no runs folder at {runs}", file=sys.stderr)
        return 1
    t0 = time.time()
    idx = load_or_build(runs, args.rebuild)
    counts = ", ".join(f"{c['key']} {sum(c['statuses'].values())}" for c in idx["collections"])
    print(f"index: {len(idx['rows'])} runs ({counts}); {len(idx['errors'])} unreadable; built {idx['built_at']} in "
          f"{idx['build_seconds']} s; loaded in {time.time() - t0:.2f} s", flush=True)
    for e in idx["errors"]:
        print(f"  unreadable {e['id']}: {e['error']}", file=sys.stderr)
    Handler.viewer = Viewer(idx)
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
