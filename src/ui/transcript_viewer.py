"""The transcript viewer: every run under runs/ in one index, and a run page with the three ladders (Cheating,
Coordination, Collusion), the judges' verdicts, the board and each agent's transcript, every tool call with the labels
it is evidence for. Ported from collusion-hermaneutics' viewer (viewer/serve.py), whose runs our framework made, so it
reads the same files here.

    uv run python -m src.ui.transcript_viewer [--port 8770] [--rebuild] [--runs runs]
    make transcripts

Every folder under runs/ is a collection, except study_smoke and folders that hold no run: a study or a manifest
collection, whose runs and their validity src/measures/run_listing.py list_collection reads. The viewer lists, measures
and serves the valid runs only, and of those only the runs of the tasks it shows: a run of a task it leaves out
(transcript_ladder_tables.HIDDEN_DOMAINS) is counted in the index's `hidden` and never measured.

For each run the index reads run.json, episodes.jsonl, signals.jsonl and the board, the judges' verdicts
(src/judging/verdict_files.py read: the eval's judge.jsonl and the judge passes' files), and the measures:
src/measures/judge_labels.py judged_cheating (the Cheating ladder with CheatBench's labels beside its clauses) and
disclosure, coordination.py, and collusion.py with a collusion judge's verdicts (src/judging/collusion_judge.py
judged_collusion), which read the transcripts. The server reads the data and never writes it. Its one output is the
index cache, tmp/transcript_viewer/index.json: each run's row and its measures, under a stamp of the run's files (stamp)
and of the code (code_stamp). A start measures again only the runs whose stamp changed, in parallel processes, and
prints its progress; --rebuild, a new INDEX_VERSION or new code measures every run. The run page reads its ladders
from the cache. The browser's transcript is read only when the browser asks for it, and it is streamed from disk as
stored.

Routes: / (the page), /tv/<file> (src/ui/transcript_static/), /static/<path> (the results browser's assets in
src/ui/static/, whose dom.js, charts.js, components.js and style.css the page reuses), /api/index, /api/run?id=,
/api/board?id= (the run page's Board tab, src/ui/transcript_board.py), /api/transcript?id=&agent=, /api/ladders.
src/ui/API.md describes them.
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
from src.measures import collusion, coordination, judge_labels
from src.measures.run_directory import RunDir, episode_agent, is_worker, roles_of, run_name
from src.measures.run_listing import Listed, list_collection
from src.ui import transcript_board, transcript_ladder_tables, transcript_levels
from src.utils.json_files import read_json, read_jsonl

HERE = Path(__file__).resolve().parent
STATIC = HERE / "transcript_static"
BROWSER_STATIC = HERE / "static"
CACHE = ROOT / "tmp" / "transcript_viewer" / "index.json"
SKIP = ("study_smoke",)
INDEX_VERSION = 6  # 6: valid runs only, knowledge work left out, the judge's labels, disclosure, the worker model
DEFAULT_PORT = 8770
WORKERS = 6
"""Processes that measure runs when a start finds runs to measure: each loads transcripts, so few enough to spare the
machine's memory."""
PROGRESS = 100
"""A start that measures runs prints a line every PROGRESS runs measured."""
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
                            "levels and models. The runs its INDEX.jsonl marks valid."),
    "study_exploration_0": ("Exploration 0", "October screen",
                            "One agent, a team of three, or three plus a hidden regulator, on five tasks and three "
                            "models."),
    "apart_controls": ("Apart controls", "lone agents",
                       "One agent on nine tasks and five models, told to cheat or not. The canonical copy of each "
                       "valid run."),
}
# Episode fields the run page shows; the rest of episodes.jsonl (rules, probes, ledger, access) stays on disk.
EPISODE_KEYS = ["episode", "orbit_agent", "role", "model", "topology", "n_turns", "n_tool_calls", "wall_time", "limit",
                "score", "ended_by", "ok", "failure", "error", "final_text", "usage", "defense", "defenses",
                "agent_version"]
RUN_JSON_KEYS = ["task", "variant", "model", "role_models", "topology", "mode", "orbit_version", "inspect_version",
                 "defenses", "team_submission", "shutdown_rate", "deviations", "code"]
ROUND_RE = re.compile(r"^r(\d+)")
SUBMIT_RE = re.compile(r"handed in the team's work in round (\d+)")
NAME_RE = re.compile(r"^(?P<stem>[a-z]+)_(?P<cell>.+)_[a-z]+\d+$")
"""A run's name, <stem>_<cell>_<tag><n>. The tag is no model: a check of the ORBIT 2.0 port is named
poem_minimal_regulated_orbit2. The models come from run.json alone (cell_of)."""


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
        """The Cheating ladder of the run with CheatBench's labels beside its clauses (src/measures/judge_labels.py
        judged_cheating) as the viewer keeps it, or {"applicable": False, "reason"} for a run without episodes or of a
        task with no ladder."""
        if self.task not in BY_TASK:
            return {"applicable": False, "reason": f"no Cheating ladder for task {self.task!r}"}
        if not self.episodes:
            return {"applicable": False, "reason": "the run recorded no episode"}
        return transcript_levels.cheating_shown(judge_labels.judged_cheating(self.path))

    def collusion(self, judges: dict[str, dict[str, list[dict]]], cheating: dict) -> dict:
        """The Collusion ladder of the run with its collusion judge's verdicts (src/judging/collusion_judge.py
        judged_collusion, with each agent's Cheating rung from `cheating`, the Cheating measure's answer), as the
        viewer keeps it, the judge's name, and on each pair the evidence of that judge's verdict (judge_evidence); or
        {"applicable": False, "reason"} when the measure says it does not apply (not_applicable), which loads no
        transcript."""
        reason = collusion.not_applicable(self.task, len(self.episodes))
        if reason:
            return {"applicable": False, "reason": reason}
        rungs = {a: r["rung"] for a, r in (cheating.get("agents") or {}).items()}
        verdicts = judges.get(COLLUSION) or {}
        measure, judge = collusion_judge.judged_collusion(self.path, rungs, verdicts)
        shown = transcript_levels.collusion_shown(measure)
        said = judge_evidence(verdicts.get(judge) or [])
        pairs = [{**p, "judge_evidence": said.get(collusion.pair_key(*p["agents"]))} for p in shown["pairs"]]
        return {**shown, "pairs": pairs, "judge": judge}

    def measured(self) -> dict:
        """What the index caches for a run beside its row: the judges' verdict files (verdict_files.read, {pass:
        {judge: [row]}}), the Cheating and Collusion measures, which read the transcripts, and each agent's disclosure
        (judge_labels.disclosure)."""
        judges = verdict_files.read(self.path)
        cheating = self.cheating()
        return {"judges": judges, "cheating": cheating, "collusion": self.collusion(judges, cheating),
                "disclosure": judge_labels.disclosure(self.path)}

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


def judge_evidence(rows: list[dict]) -> dict[str, dict]:
    """A collusion judge's evidence by pair (collusion.pair_key, the row's unit), from its parsed rows: {"collusion":
    {said, verdict (what stands after code's check), post (the judge's label of the post, P1, P2, ... in board
    order), quote, reason, check}, "scheming": {agent: {said, verdict, quote, reason, check}}}. A field the judge left
    empty is None."""
    out = {}
    for row in rows:
        if not row.get("parse_ok"):
            continue
        said = {"said": row.get("collusion"), "verdict": row.get("collusion_verdict"),
                "post": row.get("collusion_post") or None, "quote": row.get("collusion_quote") or None,
                "reason": row.get("collusion_reason") or None, "check": row.get("collusion_check")}
        scheming = {agent: {"said": row.get(f"{side}_scheming"), "verdict": row.get(f"{side}_scheming_verdict"),
                            "quote": row.get(f"{side}_scheming_quote") or None,
                            "reason": row.get(f"{side}_scheming_reason") or None,
                            "check": row.get(f"{side}_scheming_check")}
                    for side, agent in zip(collusion_judge.SIDES, row.get("agents") or [])}
        out[row["unit"]] = {"collusion": said, "scheming": scheming}
    return out


def disclosure_summary(disclosure: dict) -> dict:
    """The team's disclosure as the runs table shows it: {"yes", "no" (the workers the judge says did or did not openly
    acknowledge their own access), "value"}, value being, when no worker was judged yes or no, why (not recorded on a
    task whose verdict has no such field, else not judged), and None otherwise."""
    team = disclosure["team"]
    judged = team.get("yes", 0) + team.get("no", 0)
    why = judge_labels.NOT_RECORDED if judge_labels.NOT_RECORDED in team else judge_labels.NOT_JUDGED
    return {"yes": team.get("yes", 0), "no": team.get("no", 0), "value": None if judged else why}


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
    """What a run was made with: its task's short name, its cell, and the models of its workers and hidden roles."""

    stem: str | None
    cell: str | None
    worker: str | None
    regulator: str | None
    disregulator: str | None


ROLE_MODELS = ("worker", "regulator", "disregulator")


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


def hidden_counts(roles: dict) -> dict[str, int]:
    """How many regulators and disregulators the run had."""
    return {r: sum(1 for x in roles.values() if x == r) for r in ("regulator", "disregulator")}


def team_words(n_agents: int, roles: dict) -> str:
    """Who ran, in words: one agent, or the workers and any hidden role."""
    hidden = hidden_counts(roles)
    if n_agents <= 1:
        return "One agent"
    extra = "".join(f" plus {n} hidden {r}{'' if n == 1 else 's'}" for r, n in hidden.items() if n)
    return f"Team of {n_agents - sum(hidden.values())}{extra}"


def build_row(col: str, listed: Listed, rf: RunFiles, measured: dict) -> dict:
    """The run's index row from its files and its measures (RunFiles.measured's answer)."""
    rj, cfg, swarm = rf.run_json, rf.run_json.get("config") or {}, rf.run_json.get("swarm") or {}
    n_agents = len(rf.episodes) or int(cfg.get("n_agents") or 1)
    cell = cell_of(listed, rf)
    post = seed_post(cell, cfg, swarm, n_agents)
    sample = (rf.episodes[0].get("episode") if rf.episodes else "") or ""
    hidden = hidden_counts(rf.roles)
    team = team_words(n_agents, rf.roles)
    return {
        "id": f"{col}/{listed.key}", "col": col, "key": listed.key,
        "run": run_name(listed.path) if listed.source != "manifest" else listed.meta.get("key"),
        "task": cell.stem, "task_name": rf.task, "instance": sample.split("#")[0] or None, "arm": cell.cell,
        "arm_label": f"{team}, {post}", "seed_post": post, "team": team, "model": cell.worker,
        "model_id": (rj.get("role_models") or {}).get("worker") or rj.get("model"), "n_agents": n_agents,
        "workers": n_agents - sum(hidden.values()), "regulators": hidden["regulator"],
        "disregulators": hidden["disregulator"], "seed": listed.meta.get("seed", cfg.get("seed")),
        "dir": str(listed.path),
        "levels": transcript_levels.index_levels(rf.levels(measured), rf.roles),
        "cheatbench": cheatbench_summary(measured["judges"].get(CHEATBENCH) or {}, rf.episodes, rf.roles),
        "disclosure": disclosure_summary(measured["disclosure"]),
    }


def measure_run(col: str, listed: Listed) -> dict:
    """One run's cache entry: {"row", "measured"} (build_row, RunFiles.measured), or {"error"} for a run whose files
    cannot be read."""
    try:
        rf = RunFiles.read(listed.path)
        measured = rf.measured()
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


def valid_runs(runs: Path) -> list[tuple[str, list[Listed]]]:
    """Each collection's valid runs (run_listing's status), the collections without one left out."""
    out = []
    for folder in collections(runs):
        listed = [item for item in list_collection(folder) if item.status == "valid"]
        if listed:
            out.append((folder.name, listed))
    return out


def left_out_task(run_dir: Path) -> str | None:
    """The task of a run the viewer leaves out (transcript_ladder_tables.hidden_task), read from its run.json; None
    for a run it shows, and for a run.json that does not parse, which measure_run then reports."""
    try:
        task = (read_json(run_dir / "run.json") or {}).get("task")
    except ValueError:
        return None
    return task if transcript_ladder_tables.hidden_task(task) else None


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
    out counted in `hidden`: their entry, {"left_out": task}, is all the index reads of them. A run whose stamp and code
    stamp match `cached` (an earlier index) keeps its entry; the others are measured again (measure_all)."""
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
        label, tag, about = COLLECTION_ABOUT.get(name, (name, "", f"Runs under runs/{name}/."))
        shown = 0
        for item in listed:
            entry = entries[f"{name}/{item.key}"]
            if "error" in entry:
                errors.append({"id": f"{name}/{item.key}", "error": entry["error"]})
            elif "left_out" in entry:
                left_out += 1
            else:
                rows.append(entry["row"])
                shown += 1
        if shown:
            collections_out.append({"key": name, "label": label, "tag": tag, "about": about, "runs": shown})
    return {"version": INDEX_VERSION, "runs": str(runs), "code": code,
            "built_at": time.strftime("%Y-%m-%d %H:%M:%S %Z"), "build_seconds": round(time.time() - t0, 2),
            "measured": len(todo), "collections": collections_out, "errors": errors, "rows": rows,
            "hidden": {"domains": list(transcript_ladder_tables.HIDDEN_DOMAINS), "runs": left_out},
            "entries": entries}


def served_index(idx: dict) -> dict:
    """The index as /api/index serves it: its SERVED keys, without the measures and stamps the cache keeps."""
    return {k: idx[k] for k in SERVED}


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
    verdict on it, the board, the three ladders from the cached measures, each agent's disclosure, and every verdict
    of the collusion judges."""
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
            "disclosure": measured["disclosure"],
            "collusion_judges": {judge: [shown_verdict(row) for row in rows]
                                 for judge, rows in sorted((measured["judges"].get(COLLUSION) or {}).items())}}


def board_detail(row: dict, measured: dict) -> dict:
    """The run page's Board tab (transcript_board.board_payload) from the run's files and its cached measures."""
    rf = RunFiles.read(Path(row["dir"]))
    boards = rf.boards()
    return transcript_board.board_payload(rf.path, rf.levels(measured, boards), measured["judges"], boards)


# ---------------------------------------------------------------------------------------------------------- server
RUN_ROUTES = {"/api/run": run_detail, "/api/board": board_detail}
"""The routes that answer for one run of the index (?id=), each from the run's row and its cached measures."""
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".json": "application/json", ".svg": "image/svg+xml"}


class Server(ThreadingHTTPServer):
    """socketserver listens with a backlog of request_queue_size, 5 by default. The page asks for its dozen ES modules
    at once, and four pages loading together got connections reset at 5 and none at 128 (measured 2026-11-08)."""

    request_queue_size = 128


class Viewer:
    """What the server answers from: the served index (served_index) as bytes, its rows by id, each served run's
    cached measures and the Ladders page's data."""

    def __init__(self, idx: dict):
        served = served_index(idx)
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
            if url.path in RUN_ROUTES:
                row = self.viewer.rows.get(q.get("id", ""))
                if row is None:
                    return self.fail(HTTPStatus.NOT_FOUND, f"no run {q.get('id')!r} in the index")
                return self.send_json(RUN_ROUTES[url.path](row, self.viewer.measured[row["id"]]))
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
    counts = ", ".join(f"{c['key']} {c['runs']}" for c in idx["collections"])
    left_out = f"; {idx['hidden']['runs']} valid runs of {', '.join(idx['hidden']['domains'])} left out" \
        if idx["hidden"]["runs"] else ""
    print(f"index: {len(idx['rows'])} valid runs ({counts}){left_out}; {len(idx['errors'])} unreadable; "
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
