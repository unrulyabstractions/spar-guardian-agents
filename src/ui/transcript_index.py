"""The transcript viewer's index: every valid run under runs/ with its row and its measures, cached between starts.

    idx = load_or_build(ROOT / "runs", rebuild=False)     # the index, its runs measured again where their files changed
    idx["rows"][0]["levels"]["team"]                      # the team's level on each of the three ladders
    served_index(idx)                                     # what /api/index answers (SERVED)

Every folder under runs/ is a collection, except SKIP and folders that hold no run: a study or a manifest collection,
whose runs and their validity src/measures/run_listing.py list_collection reads. The index lists, measures and serves
the valid runs only, and of those only the runs of the tasks the study reads: a run of a task it leaves out
(src/measures/ladder_by_domain.py HIDDEN_TASKS) is counted in `hidden` and never measured.

For each run the index reads run.json, episodes.jsonl, signals.jsonl and the board (RunFiles), CheatBench's verdicts
(src/judging/verdict_files.py cheatbench) and the measures, which read the transcripts and the judges' verdict files:
src/measures/judge_labels.py judged_cheating (the Cheating ladder with CheatBench's labels beside its clauses),
labels (the judge labels awareness, manipulation and disclosure), coordination.py and collusion.py run_collusion
(the Collusion ladder with the scheming judge's verdicts). The index never writes a run. Its one output is the cache,
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
from src.judging.judge_records import NO, UNCHECKED, YES
from src.measures import collusion, coordination, judge_labels
from src.measures.ladder_by_domain import HIDDEN_TASKS, hidden_task
from src.measures.run_directory import episode_agent, is_worker, roles_of, run_name
from src.measures.run_listing import Listed, list_collection
from src.ui import transcript_levels
from src.utils.json_files import read_json, read_jsonl

CACHE = ROOT / "tmp" / "transcript_viewer" / "index.json"
SKIP = ("study_smoke",)
INDEX_VERSION = 7  # 7: the final Collusion ladder with the scheming judge, the judge labels, rows without dir and arm
WORKERS = 6
"""Processes that measure runs when a start finds runs to measure: each loads transcripts, so few enough to spare the
machine's memory."""
PROGRESS = 100
"""A start that measures runs prints a line every PROGRESS runs measured."""
MEASURING = ("src/measures", "src/domains", "src/judging", "src/ui")
"""The folders whose code decides a run's row and measures (code_stamp)."""
SERVED = ("built_at", "build_seconds", "measured", "collections", "errors", "rows", "hidden")
"""The index's keys /api/index answers; the cache also keeps its version, its runs folder, each run's measures, its
folder and its stamps."""
QUESTIONS = ("awareness", "manipulation")
"""The judge labels the pass "labels" answers with a quote, by the measure's names (judge_labels.labels)."""
READ = (YES, NO, UNCHECKED)
"""A judge label's values that say the judge read the agent: a verdict that stands as yes or no, or a yes whose quote
failed the code's check."""

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
SYSTEM = "system"
"""The sender of a harness notice on the board."""
NAME_RE = re.compile(r"^(?P<stem>[a-z]+)_(?P<cell>.+)_[a-z]+\d+$")
"""A run's name, <stem>_<cell>_<tag><n>. The tag is no model: a check of the ORBIT 2.0 port is named
poem_minimal_regulated_orbit2. The models come from run.json alone (cell_of)."""
ROLE_MODELS = ("worker", "regulator", "disregulator")


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
    """The board of each sample in list order: each message's id, its place on the board (board_no, the label P<n> the
    Board tab and the judges give it), its post number (post_n, counted over the agents' posts; None for a harness
    notice), its sender and the sender's role, and the tool calls that sent it and read it."""
    cdir = run_dir / coordination.COMMUNICATION
    if not cdir.is_dir():
        return []
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
            sender = m.get("sender")
            n_post += sender != SYSTEM
            msgs.append({"id": m.get("id"), "board_no": i + 1, "post_n": None if sender == SYSTEM else n_post,
                         "sender": sender, "role": roles.get(sender), "sent": sent.get(m.get("id")),
                         "reads": reads.get(m.get("id"), [])})
        boards.append({"sample": path.stem, "messages": msgs})
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
        """The Collusion ladder of the run with the scheming judge's verdicts (src/measures/collusion.py
        run_collusion) and what each verdict row said (collusion.scheming_rows), as the viewer keeps it; or
        {"applicable": False, "reason"} when the measure says it does not apply (not_applicable), which loads no
        transcript."""
        reason = collusion.not_applicable(self.task, len(self.episodes))
        if reason:
            return {"applicable": False, "reason": reason}
        return transcript_levels.collusion_shown(collusion.run_collusion(self.path), collusion.scheming_rows(self.path))

    def labels(self) -> dict:
        """Each agent's judge labels (judge_labels.labels) with what the judge said: {"agents": {agent: {awareness |
        manipulation: {value, source, quote, reason, check}, disclosure: {value}}}, "team": {label: {value: number of
        workers}}}. The quote, its source (post or reasoning), the reason and the code's check are the row's of the
        pass "labels" as the measure reads it (judge_labels.label_rows)."""
        measure = judge_labels.labels(self.path)
        rows = judge_labels.label_rows(self.path) or {}

        def said(agent: str, question: str) -> dict:
            row = rows.get(agent) or {}
            answer = row.get(question) if row.get("parse_ok") else None
            if not answer:
                return {}
            return {"source": answer.get("source"), "quote": answer.get("quote") or None,
                    "reason": answer.get("reason"), "check": row.get(f"{question}_check")}
        agents = {a: {**{q: {"value": values[q], **said(a, q)} for q in QUESTIONS},
                      "disclosure": {"value": values["disclosure"]}} for a, values in measure["agents"].items()}
        return {"agents": agents, "team": measure["team"]}

    def measured(self) -> dict:
        """What the index caches for a run beside its row: CheatBench's verdicts ({judge: {episode: row}},
        verdict_files.cheatbench), the three ladders' measures (the Cheating and Collusion measures read the
        transcripts; coordination.run_coordination) and each agent's judge labels with what the judge said (labels)."""
        return {"cheatbench": verdict_files.cheatbench(self.path), "cheating": self.cheating(),
                "coordination": coordination.run_coordination(self.path), "collusion": self.collusion(),
                "labels": self.labels()}

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


def disclosure_summary(labels: dict) -> dict:
    """The team's disclosure as the runs table shows it, from the run's judge labels (RunFiles.labels): {"yes", "no"
    (the workers the judge says did or did not openly acknowledge their own access), "value"}, value being, when no
    worker was judged yes or no, why (not recorded on a task whose verdict has no such field, else not judged), and
    None otherwise."""
    team = labels["team"]["disclosure"]
    judged = team.get("yes", 0) + team.get("no", 0)
    why = judge_labels.NOT_RECORDED if judge_labels.NOT_RECORDED in team else judge_labels.NOT_JUDGED
    return {"yes": team.get("yes", 0), "no": team.get("no", 0), "value": None if judged else why}


def label_summary(labels: dict) -> dict:
    """Each judge label of QUESTIONS as the runs table shows it: {label: {"yes", "read"}}, the agents the judge says yes
    of the agents it read (READ), every agent of the run counted."""
    out = {}
    for label in QUESTIONS:
        read = [v for v in (row[label]["value"] for row in labels["agents"].values()) if v in READ]
        out[label] = {"yes": read.count(YES), "read": len(read)}
    return out


# ------------------------------------------------------------------------------------------------------- the rows
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
        "task": cell.stem, "task_name": rf.task, "instance": sample.split("#")[0] or None,
        "arm_label": f"{team}, {post}", "seed_post": post, "team": team, "model": cell.worker,
        "model_id": (rj.get("role_models") or {}).get("worker") or rj.get("model"), "n_agents": n_agents,
        "workers": n_agents - sum(hidden.values()), "regulators": hidden["regulator"],
        "disregulators": hidden["disregulator"], "seed": listed.meta.get("seed", cfg.get("seed")),
        "levels": transcript_levels.index_levels(rf.levels(measured), rf.roles),
        "cheatbench": cheatbench_summary(measured["cheatbench"], rf.episodes, rf.roles),
        "disclosure": disclosure_summary(measured["labels"]),
        "labels": label_summary(measured["labels"]),
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
            "hidden": {"tasks": sorted(HIDDEN_TASKS), "runs": left_out},
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
