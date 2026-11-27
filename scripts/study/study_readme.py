"""Each study's README.md, generated from its own files and the code: runs/<study>/README.md.

    uv run python -m scripts.study.study_readme runs/ian_baseline_explorations_1          # write the README
    uv run python -m scripts.study.study_readme runs/ian_baseline_explorations_1 --check  # write nothing; 1 if stale
    uv run python -m scripts.study.study_readme --all [--check]                           # every study in STUDIES here

A README holds what the study is (its `about` in scripts/study/study_readme_fields.yaml), its layout as a tree with
counts, every file type a run holds with its schema (each field, the JSON types it takes in this study, and one line on
what it means), how the files link (with how many links resolve), the three ladders' levels (src/ui/transcript_levels.py
LADDERS) and the judge labels' anchors (src/measures/judge_labels.py SCORE_ANCHORS), and five lines that load the study
in Python.

The fields and their types are read from the study's runs (run_directory.study_runs; every run.json folder for the
apart controls), so a README names only what its study holds. Each field's meaning comes from the YAML file, and a
CheatBench verdict field's from its task's judge schema (submodules/cheatbench/tasks/<task>/judge_schema.py, the
first sentence of the field's description). render refuses a field without a meaning and a run file no spec covers
(Undescribed), so a README never leaves a field or a file out. The smoke suites inside a study are in the runs' layout
and are listed in the tree, uncounted (each smoke run adds runs), not described.

Reads only: no model call, no Docker. tests/unit/test_study_readme.py checks that every README here is current.
"""
from __future__ import annotations

import argparse
import ast
import fnmatch
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import yaml

from src.configuration.settings import ROOT
from src.measures.judge_labels import SCORE_ANCHORS
from src.measures.run_directory import FILES, GROUPS, RunDir, study_runs
from src.ui.transcript_levels import LADDERS, ORDER, label_about
from src.utils import progress

FIELDS_FILE = Path(__file__).with_name("study_readme_fields.yaml")
README = "README.md"
STUDIES = ("disregulator_existence_2", "regulator_scale_2", "regulator_headline_0", "disregulator_headline_0",
           "disregulator_workers_0", "disregulator_workers_1", "ian_seed_prototyping_2", "ian_seed_prototyping_1", "ian_seed_prototyping_0",
           "ian_baseline_explorations_0", "ian_baseline_explorations_1", "study_smoke", "study_orbit2_check",
           "apart_controls")
"""The studies that get a README, in the order --all writes them."""
MANIFEST_STUDY = "apart_controls"
"""The collection whose runs sit at <model>/<task>/<cell>/s<seed>/ with a manifest.jsonl, not in groups."""
CHEATBENCH_TASKS = ROOT / "submodules" / "cheatbench" / "tasks"
IGNORED = {".DS_Store"}
SUITE = "a smoke-test suite, runs nested by stamp or case, not described"
"""The line of a folder of a study that is neither a run nor a group."""
JSON_TYPES = {dict: "object", list: "array", str: "string", bool: "bool", int: "int", float: "float",
              type(None): "null"}
TYPE_ORDER = ("string", "int", "float", "bool", "object", "array", "null")
MAX_VALUES = 8
"""A string field with at most this many values lists them."""
MAX_VALUE_CHARS = 48
NO_VALUES = ("sha256", "_id", "_at", "_evidence", "agent", "episode", "commit", "exe", "cwd", "error", "final_text",
             "summary", "content", "path")
"""Fields by suffix whose values a README never lists: hashes, ids, times, names and free text."""


class Undescribed(ValueError):
    """A field without a meaning, or a run file no spec covers."""


@dataclass(frozen=True)
class Spec:
    """One table of a README: the rows of the files matching `glob` (from the run's folder), or the items at `items`
    (a dotted path to a list) of each, or the rows whose `row` key equals `where`."""
    key: str
    glob: str
    title: str
    items: str | None = None
    where: str | None = None
    by_task: bool = False


SPECS = (
    Spec("run", FILES["run.json"], FILES["run.json"]),
    Spec("episodes", FILES["episodes.jsonl"], FILES["episodes.jsonl"]),
    Spec("signals_agent", FILES["signals.jsonl"], f"{FILES['signals.jsonl']}, agent rows (`\"row\": \"agent\"`)",
         where="agent"),
    Spec("signals_team", FILES["signals.jsonl"], f"{FILES['signals.jsonl']}, the team row (`\"row\": \"team\"`)",
         where="team"),
    Spec("cheatbench", FILES["judges"] + "cheatbench.*.jsonl", FILES["judges"] + "cheatbench.<judge>.jsonl",
         by_task=True),
    Spec("labels", FILES["judges"] + "labels.*.jsonl", FILES["judges"] + "labels.<judge>.jsonl"),
    Spec("access_log", FILES["access_log.jsonl"], FILES["access_log.jsonl"]),
    Spec("network", FILES["network.jsonl"], FILES["network.jsonl"]),
    Spec("communication", FILES["communication"] + "*.json", FILES["communication"] + "<sample>.json"),
    Spec("messages", FILES["communication"] + "*.json", FILES["communication"] + "<sample>.json" + " `messages[]`",
         items="messages"),
    Spec("events", FILES["communication"] + "*.json", FILES["communication"] + "<sample>.json" + " `events[]`",
         items="events"),
    Spec("defense", FILES["defense"] + "*.json", FILES["defense"] + "<sample>.json"),
    Spec("transcript", FILES["trajectories"] + "*/transcript.json",
         FILES["trajectories"] + "<episode>/transcript.json"),
    Spec("trajectory", FILES["trajectories"] + "*/transcript.json", "transcript.json `trajectory`",
         items="trajectory"),
    Spec("atif_steps", FILES["trajectories"] + "*/transcript.json", "transcript.json `trajectory.steps[]`",
         items="trajectory.steps"),
    Spec("steps", FILES["steps.jsonl"], FILES["steps.jsonl"]),
    Spec("replay", FILES["replay.json"], FILES["replay.json"]),
    Spec("tmp_snapshots", FILES["tmp_snapshots.json"], FILES["tmp_snapshots.json"]),
    Spec("swe_summary", FILES["swe_summary.json"], FILES["swe_summary.json"]),
    Spec("docent_summary", FILES["tools"] + "docent/summary.json", FILES["tools"] + "docent/summary.json"),
)
"""The tables, in README order. A spec whose files the study lacks has no table."""


# --------------------------------------------------------------------------
# reading
# --------------------------------------------------------------------------
def meanings() -> dict:
    """scripts/study/study_readme_fields.yaml: {files, opaque, study_files, links, studies}."""
    return yaml.safe_load(FIELDS_FILE.read_text())


def runs_of(study: Path) -> list[Path]:
    """The study's runs: run_directory.study_runs, or every folder with a run.json for the apart controls."""
    if study.name == MANIFEST_STUDY:
        return sorted(path.parent.parent for path in study.rglob(FILES["run.json"]))
    return study_runs(study)


def json_type(value) -> str:
    return JSON_TYPES[type(value)]


def rows_of(path: Path) -> list:
    """A file's rows: each line of a .jsonl, else the one value of a .json."""
    text = path.read_text()
    if path.suffix == ".jsonl":
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    return [json.loads(text)]


def at(value, dotted: str):
    """The value at a dotted path of objects, or None."""
    for part in dotted.split("."):
        value = value.get(part) if isinstance(value, dict) else None
    return value


@dataclass
class Table:
    """What one spec found: the files, the rows, and per field (in first-seen order) its types and how many rows hold
    it; by task when the spec asks."""
    files: int = 0
    rows: int = 0
    types: dict[str, Counter] = field(default_factory=dict)
    held: Counter = field(default_factory=Counter)
    tasks: dict[str, set] = field(default_factory=lambda: defaultdict(set))
    task_rows: Counter = field(default_factory=Counter)
    values: dict[str, set] = field(default_factory=lambda: defaultdict(set))

    def add(self, row: dict, task: str | None) -> None:
        self.rows += 1
        self.task_rows[task] += 1
        for key, value in row.items():
            self.types.setdefault(key, Counter())[json_type(value)] += 1
            self.held[key] += 1
            self.tasks[key].add(task)
            if isinstance(value, str) and len(self.values[key]) <= MAX_VALUES:
                self.values[key].add(value)

    def value_list(self, key: str) -> str:
        """The field's string values, when it takes at most MAX_VALUES short ones: "Values: `a`, `b`." """
        values = self.values.get(key) or set()
        if not values or len(values) > MAX_VALUES or key.endswith(NO_VALUES) \
                or any(len(v) > MAX_VALUE_CHARS or "\n" in v for v in values):
            return ""
        return "Values: " + ", ".join(f"`{v}`" for v in sorted(values)) + "."


def scan(study: Path, runs: list[Path]) -> tuple[dict[str, Table], Counter, list[str]]:
    """Every spec's table over the study's runs, the count of each opaque file pattern, and the run files no spec and
    no opaque pattern covers."""
    opaque = meanings()["opaque"]
    tables = {spec.key: Table() for spec in SPECS}
    opaque_counts: Counter = Counter()
    unknown: set[str] = set()
    for run in runs:
        for path in sorted(p for p in run.rglob("*") if p.is_file() and p.name not in IGNORED):
            rel = path.relative_to(run).as_posix()
            specs = [spec for spec in SPECS if fnmatch.fnmatch(rel, spec.glob)]
            if not specs:
                pattern = next((p for p in opaque if fnmatch.fnmatch(rel, p)), None)
                if pattern is None:
                    unknown.add(re.sub(r"[0-9a-f]{6,}", "<hash>", rel))
                else:
                    opaque_counts[pattern] += 1
                continue
            rows = rows_of(path)
            for spec in specs:
                table = tables[spec.key]
                table.files += 1
                for row in rows:
                    if spec.where is not None and (not isinstance(row, dict) or row.get("row") != spec.where):
                        continue
                    items = at(row, spec.items) if spec.items else row
                    items = items if isinstance(items, list) else [items] if isinstance(items, dict) else []
                    task = row.get("task") if spec.by_task else None
                    for item in items:
                        if isinstance(item, dict):
                            table.add(item, task)
    return tables, opaque_counts, sorted(unknown)


# --------------------------------------------------------------------------
# CheatBench's verdict fields
# --------------------------------------------------------------------------
def first_sentence(text: str) -> str:
    text = " ".join(text.split())
    match = re.match(r"(.+?[.;])(\s|$)", text)
    return (match.group(1) if match else text).rstrip(";.") + "."


def schema_descriptions(task: str) -> dict[str, str]:
    """{field: the first sentence of its description} from the task's judge schema files, read as text (no import):
    judge_schema.py first, then the task folder's other modules."""
    folder = CHEATBENCH_TASKS / task
    out: dict[str, str] = {}
    if not folder.is_dir():
        return out
    files = [folder / "judge_schema.py", *sorted(p for p in folder.glob("_*.py"))]
    for path in files:
        if not path.is_file():
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if not (isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
                    and isinstance(node.value, ast.Call)):
                continue
            for keyword in node.value.keywords:
                if keyword.arg == "description":
                    try:
                        text = ast.literal_eval(keyword.value)
                    except ValueError:
                        continue
                    out.setdefault(node.target.id, first_sentence(text))
    return out


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------
def type_text(types: Counter) -> str:
    return " \\| ".join(t for t in TYPE_ORDER if t in types)


def cell(text: str) -> str:
    return " ".join(str(text).split()).replace("|", "\\|")


def join_values(meaning: str, values: str) -> str:
    """The meaning, then the field's values as a sentence of their own."""
    return f"{meaning.rstrip('.')}. {values}" if values else meaning


def field_table(spec: Spec, table: Table, described: dict[str, str], missing: list[str]) -> list[str]:
    lines = ["| field | type | meaning |", "|---|---|---|"]
    for key, types in table.types.items():
        meaning = described.get(key)
        if meaning is None:
            missing.append(f"{spec.key}.{key}")
            continue
        mark = "" if table.held[key] == table.rows else "?"
        lines.append(row_line(key, mark, type_text(types), join_values(meaning, table.value_list(key))))
    return lines


def row_line(key: str, mark: str, types: str, meaning: str) -> str:
    return f"| `{key}`{mark} | {types} | {cell(meaning)} |"


def verdict_tables(spec: Spec, table: Table, described: dict[str, str], missing: list[str]) -> list[str]:
    """A CheatBench verdict file: the fields described here or held by every task's rows, then each task's own fields
    with the meaning its judge schema gives."""
    tasks = sorted(t for t in table.task_rows if t)
    shared = [k for k in table.types if k in described or all(t in table.tasks[k] for t in tasks)]
    lines = ["| field | type | meaning |", "|---|---|---|"]
    for key in shared:
        meaning = described.get(key)
        if meaning is None:
            missing.append(f"{spec.key}.{key}")
            continue
        mark = "" if table.held[key] == table.rows else "?"
        lines.append(row_line(key, mark, type_text(table.types[key]), join_values(meaning, table.value_list(key))))
    own = [k for k in table.types if k not in shared]
    if own:
        lines += ["", "Each task's own verdict fields, as its CheatBench judge schema defines them:", "",
                  "| field | type | task | meaning |", "|---|---|---|---|"]
        for task in tasks:
            schema = schema_descriptions(task)
            for key in own:
                if task not in table.tasks[key]:
                    continue
                meaning = schema.get(key) or described.get(key)
                if meaning is None:
                    missing.append(f"{spec.key}.{key} ({task})")
                    continue
                lines.append(f"| `{key}` | {type_text(table.types[key])} | {task} | {cell(meaning)} |")
    return lines


def count(n: int, word: str) -> str:
    return f"{n:,} {word}" + ("" if n == 1 else "s")


def tree(study: Path, runs: list[Path], data: dict) -> list[str]:
    """The study's folder and its log folder as a tree, each entry with its count and its one line."""
    lines = [f"runs/{study.name}/"]
    files = data["study_files"]
    names = {p.name for p in study.iterdir() if p.is_file() and p.name not in IGNORED} | {README}
    for name in sorted(names):
        if name not in files:
            raise Undescribed(f"{study.name}: {name} has no line in study_files")
        lines.append(f"  {name:<34} {cell(files[name])}")
    run_set = set(runs)
    if study.name == MANIFEST_STUDY:
        canonical = [r for r in runs if "extra_duplicate_samples" not in r.parts]
        lines.append(f"  {'<model>/<task>/<cell>/s<seed>/':<34} {count(len(canonical), 'run')}, one copy per run name")
        extra = len(runs) - len(canonical)
        lines.append(f"  {'extra_duplicate_samples/.../s<seed>__<host>/':<34} {count(extra, 'run')}, the other valid "
                     f"copies")
    else:
        top = [r for r in runs if r.parent == study]
        lines.append(f"  {'<run>/':<34} {count(len(top), 'run')} at the top level")
        for group in GROUPS:
            inside = [r for r in runs if r.parent == study / group]
            if inside:
                about = data["groups"][group]
                lines.append(f"  {group + '/<run>/':<34} {count(len(inside), 'run')}: {about}")
        for path in sorted(p for p in study.iterdir() if p.is_dir() and p.name not in GROUPS and p not in run_set):
            lines.append(f"  {path.name + '/':<34} {SUITE}")
    lines += [f"  {'<run>/' + layer:<34} {cell(about)}" for layer, about in data["run_folders"].items()]
    logs = study.resolve().parent.parent / "logs" / study.name
    if logs.is_dir():
        lines += [f"logs/{study.name}/", *log_tree(study, logs, runs, data)]
    return lines


def log_tree(study: Path, logs: Path, runs: list[Path], data: dict) -> list[str]:
    """The study's log folder by entry, with its files by suffix: the top-level runs' logs together, each group,
    every other folder with its line from log_groups, and the smoke suites' logs together, uncounted, since a smoke
    run may write there at any time."""
    if study.name == MANIFEST_STUDY:
        logs_found = Counter(path.suffix or path.name for path in logs.rglob("*") if path.is_file()
                             and path.name not in IGNORED)
        files = ", ".join(f"{n:,} {suffix}" for suffix, n in sorted(logs_found.items()))
        return [f"  {'<model>/<task>/<cell>/s<seed>/':<34} {files}: each run's eval log"]
    top = {r.name for r in runs if r.parent == study}
    entries: dict[str, Counter] = defaultdict(Counter)
    for path in sorted(p for p in logs.rglob("*") if p.is_file() and p.name not in IGNORED):
        first = path.relative_to(logs).parts[0]
        name = ("<run>" if first in top else first if first in GROUPS or first in data["log_groups"]
                else "smoke*" if first.startswith("smoke") else first)
        entries[name][path.suffix or path.name] += 1
    unknown = [name for name in entries if name not in ("<run>", "smoke*", *GROUPS, *data["log_groups"])]
    if unknown:
        raise Undescribed(f"{study.name}: log folders with no line in log_groups: {', '.join(unknown)}")
    out = []
    for name in ["<run>", *(g for g in GROUPS if g in entries), *sorted(g for g in entries if g not in GROUPS
                                                                      and g not in ("<run>", "smoke*")), "smoke*"]:
        if name not in entries:
            continue
        files = "" if name == "smoke*" else ", ".join(f"{n:,} {suffix}" for suffix, n in sorted(entries[name].items()))
        about = {"<run>": "each top-level run's eval log", "smoke*": "the smoke-test suites' logs"}.get(name) or (
            f"the eval logs of {name}/<run>/" if name in GROUPS else data["log_groups"][name])
        label = name + "/" + ("<run>/" if name in GROUPS else "")
        out.append(f"  {label:<34} {files + ': ' if files else ''}{about}")
    return out


def resolves(paths: list[tuple[Path, str]]) -> str:
    return share([(base / rel).exists() for base, rel in paths])


def share(hits: list[bool]) -> str:
    return f"{sum(hits):,} of {len(hits):,}"


def link_rows(study: Path, runs: list[Path]) -> list[tuple[str, str]]:
    """(link, how many resolve) for every link the study's files hold."""
    eval_logs, transcripts, verdict_paths, prompts, units, signal_eps, samples = [], [], [], [], [], [], []
    for run in runs:
        stored = RunDir(run)
        run_json = stored.run_json
        if run_json.get("eval_log"):
            eval_logs.append((ROOT, run_json["eval_log"]))
        episodes = stored.episodes()
        names = {ep["episode"] for ep in episodes}
        keys = {f"{ep['id']}#{ep['replicate']}" for ep in episodes}
        transcripts += [(stored.processed, ep["transcript_path"]) for ep in episodes if ep.get("transcript_path")]
        for path in sorted(stored.file("judges").glob("*.jsonl")):
            for row in rows_of(path):
                if row.get("transcript_path"):
                    verdict_paths.append((stored.processed, row["transcript_path"]))
                prompt = row.get("judge_prompt")
                prompts += [(ROOT, p) for p in ([prompt] if isinstance(prompt, str) else prompt or [])]
                units.append(row.get("unit") in names or row.get("episode") in names)
        signal_eps += [row["episode"] in names for row in stored.signals()]
        samples += [p.stem in keys for p in stored.file("communication").glob("*.json")]
    out = [("run.json `eval_log` → the eval log, from the checkout", resolves(eval_logs)),
           ("episodes.jsonl `transcript_path` → the transcript, from the run's processed/", resolves(transcripts))]
    if verdict_paths:
        out.append(("verdict `transcript_path` → the transcript, from the run's processed/", resolves(verdict_paths)))
    if prompts:
        out.append(("verdict `judge_prompt` → the judge's prompt file(s), from the checkout", resolves(prompts)))
    if units:
        out.append(("verdict `unit` (and `episode`) → an episodes.jsonl `episode`", share(units)))
    if signal_eps:
        out.append(("signals.jsonl agent row `episode` → an episodes.jsonl `episode`",
                    share(signal_eps)))
    if samples:
        out.append(("communication/`<sample>`.json → an episode's `id`#`replicate`", share(samples)))
    index = study / "INDEX.jsonl"
    if index.is_file():
        out.append(("INDEX.jsonl `path` → the run's folder, from the study's folder",
                    resolves([(study, row["path"]) for row in rows_of(index)])))
    return out


def ladder_lines() -> list[str]:
    lines = []
    for key in ORDER:
        ladder = LADDERS[key]
        lines += [f"**{ladder['name']}** ({cell(ladder['rule'])})", "", "| level | meaning |", "|---|---|"]
        lines += [f"| `{level}` | {cell(meaning)} |" for level, meaning in zip(ladder["levels"], ladder["meanings"])]
        lines.append("")
    about = label_about()
    lines += [f"**Judge labels** ({cell(about['scored_by'])})", "", "| label | what the score measures |", "|---|---|"]
    lines += [f"| `{key}` | {cell(label['scored'])} |" for key, label in about["labels"].items()]
    lines += ["", "| score | meaning |", "|---|---|"]
    lines += [f"| {score} | {cell(meaning)} |" for score, meaning in SCORE_ANCHORS.items()]
    return lines


def commit_day(commit: str) -> str | None:
    """The commit's date (YYYY-MM-DD) from git, None when the checkout lacks it."""
    done = subprocess.run(["git", "-C", str(ROOT), "log", "-1", "--format=%cs", commit], capture_output=True,
                          text=True)
    return done.stdout.strip() or None if done.returncode == 0 else None


def markdown_table(head: list[str], rows: list[list]) -> list[str]:
    return ["| " + " | ".join(head) + " |", "|" + "---|" * len(head),
            *("| " + " | ".join(cell(v) for v in row) + " |" for row in rows)]


def composition(study: Path, runs: list[Path]) -> list[str]:
    """What the study holds: from INDEX.jsonl, each task's arms (valid of all), the models by short name and the
    commits; one line per run for a study without an index or whose index names no arm."""
    index = study / "INDEX.jsonl"
    if not index.is_file():
        return run_table(study, runs)
    rows = rows_of(index)
    if any(r["kind"] for r in rows):
        kinds = list(dict.fromkeys(r["kind"] for r in sorted(rows, key=lambda r: KIND_ORDER.get(r["kind"], 99))))
        tasks = sorted({r["cheatbench_task"] for r in rows})
        grid = [[f"`{task}`", *(cell_count(rows, task, kind) for kind in kinds)] for task in tasks]
        out = ["Runs by task and arm, valid of all (INDEX.jsonl `valid`):", "",
               *markdown_table(["task", *(f"`{k}`" for k in kinds)], grid), ""]
    else:
        out = [*run_table(study, runs), ""]
    models: dict[str, set] = defaultdict(set)
    roles: dict[str, set] = defaultdict(set)
    for r in rows:
        if r["mock"]:
            continue
        for short, model, role in ((r["worker"], r["inspect_model"], "worker"),
                                   (r["regulator"], r["regulator_inspect_model"], "regulator")):
            if short and model:
                models[short].add(model)
                roles[short].add(role)
    if models:
        out += ["Models by the short names that run names and INDEX.jsonl use (mock runs left out):", "",
                *markdown_table(["short name", "role", "model"],
                                [[f"`{s}`", ", ".join(sorted(roles[s])), ", ".join(f"`{m}`" for m in sorted(models[s]))]
                                 for s in sorted(models)]), ""]
    reasons = Counter(r["reason"] for r in rows)
    days = sorted(d for d in (commit_day(c) for c in {r["commit"] for r in rows}) if d)
    out.append(f"{sum(r['valid'] for r in rows):,} of {len(rows):,} runs are valid. Reasons: "
               + ", ".join(f"`{k}` {v:,}" for k, v in reasons.most_common()) + "."
               + (f" The runs ran at {count(len({r['commit'] for r in rows}), 'commit')} dated {days[0]} to "
                  f"{days[-1]}." if days else ""))
    return out


def run_table(study: Path, runs: list[Path]) -> list[str]:
    """One line per run: its folder, task, model, agents and the ORBIT and Inspect versions that ran it."""
    rows = []
    for run in runs:
        meta = RunDir(run).run_json
        rows.append([f"`{run.relative_to(study).as_posix()}`", meta.get("task"), meta.get("model"),
                     len(meta.get("agents") or []), meta.get("orbit_version"), meta.get("inspect_version")])
    return markdown_table(["run", "task", "model", "agents", "ORBIT", "Inspect"], rows)


KIND_ORDER = {kind: i for i, kind in enumerate(("solo", "solo_cheat", "minimal", "minimal_cheat", "minimal_all",
                                                 "minimal_regulated", "minimal_disregulated",
                                                 "minimal_regulated_disregulated"))}


def cell_count(rows: list[dict], task: str, kind: str) -> str:
    mine = [r for r in rows if r["cheatbench_task"] == task and r["kind"] == kind]
    return f"{sum(r['valid'] for r in mine)}/{len(mine)}" if mine else ""


def manifest_parts(study: Path) -> dict[str, str]:
    """The apart controls' generated tables, for the {{placeholders}} of its about file: today's Cheating rung
    against the manifest's rung, the manifest's rung counts, seeds per cell and coverage, all over the canonical
    set."""
    rows = [r for r in rows_of(study / "manifest.jsonl") if r["canonical"]]
    stats = json.loads((study / "_build_stats.json").read_text())
    models, tasks, cells = stats["models"], stats["tasks"], stats["cells"]
    old = ("none", "exposed", "tried", "opened", "used", "tampered", "checked", "shipped")
    cross: Counter = Counter()
    for r in rows:
        team = RunDir(study / r["model"] / r["task"] / r["cell"] / f"s{r['seed']}").team_signals()[0]
        cross[(r["rung"], team["cheating_rung"])] += 1
    rungs = LADDERS["cheating"]["levels"]
    crosstab = markdown_table(["manifest `rung`", *(f"`{x}`" for x in rungs)],
                              [[f"`{o}`", *(cross[(o, x)] or "" for x in rungs)] for o in old
                               if any(cross[(o, x)] for x in rungs)])
    counts = Counter(r["rung"] for r in rows)
    seeds = markdown_table(["task", *(f"{c}/{m}" for c in cells for m in models)],
                           [[t, *(sum(1 for r in rows if (r["task"], r["cell"], r["model"]) == (t, c, m))
                                  for c in cells for m in models)] for t in tasks])

    def coverage(group: list[dict]) -> list:
        return [len(group), *(sum(1 for r in group if r["label"] == x) for x in ("cheat", "honest", "other")),
                dollars(sum(r["billed_usd"] or 0 for r in group))]
    cover = markdown_table(["model", "runs", "cheat", "honest", "other", "billed USD"],
                           [*([m, *coverage([r for r in rows if r["model"] == m])] for m in models),
                            ["**all**", *(f"**{v}**" for v in coverage(rows))]])
    return {"rung_crosstab": "\n".join(crosstab),
            "rung_counts": ", ".join(f"`{o}`: {counts[o]:,}" for o in old if counts[o]),
            "seeds_table": "\n".join(seeds), "coverage_table": "\n".join(cover)}


def dollars(amount: float) -> str:
    """An amount to the cent, half up, from the exact sum (the build's _build_stats.json rounds twice: its qwen
    48.725 is 48.724988)."""
    return str(Decimal(str(round(amount, 6))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def about_text(study: Path, about: dict) -> str:
    """The study's own prose: `about`, or the file `about_file` beside this script with its {{placeholders}}
    filled."""
    if "about_file" not in about:
        return about["about"].strip()
    text = (FIELDS_FILE.parent / about["about_file"]).read_text().strip()
    for key, value in manifest_parts(study).items():
        text = text.replace("{{" + key + "}}", value)
    if "{{" in text:
        raise Undescribed(f"{study.name}: a placeholder left in {about['about_file']}")
    return text


def render(study: Path) -> str:
    """The study's README.md. Raises Undescribed when a field or a file has no meaning."""
    study = Path(study)
    data = meanings()
    about = data["studies"].get(study.name)
    if about is None:
        raise Undescribed(f"{study.name}: no entry under studies in {FIELDS_FILE.name}")
    runs = runs_of(study)
    tables, opaque_counts, unknown = scan(study, runs)
    if unknown:
        raise Undescribed(f"{study.name}: run files no spec covers: {', '.join(unknown[:20])}")
    missing: list[str] = []
    out = [f"# {about['title']}", "", about_text(study, about), ""]
    if study.name != MANIFEST_STUDY:
        out += ["## Composition", "", *composition(study, runs), ""]
    out += ["## Layout", "", "```", *tree(study, runs, data), "```", ""]
    out += ["## Files", "", data["files_note"].strip(), ""]
    study_files = [name for name in ("INDEX.jsonl", "manifest.jsonl") if (study / name).is_file()]
    for name in study_files:
        key = name.split(".")[0].lower()
        table = Table()
        table.files = 1
        for row in rows_of(study / name):
            table.add(row, None)
        spec = Spec(key, name, name)
        out += [f"### {name}", "", cell(data["files"][key]["about"]) + f" {count(table.rows, 'row')}.", ""]
        out += [*field_table(spec, table, data["files"][key]["fields"], missing), ""]
    for spec in SPECS:
        table = tables[spec.key]
        if not table.rows:
            continue
        entry = data["files"].get(spec.key)
        if entry is None:
            raise Undescribed(f"{study.name}: {spec.title} has no entry under files")
        out += [f"### {spec.title}", "",
                cell(entry["about"]) + f" {count(table.files, 'file')}, {count(table.rows, 'row')}.", ""]
        builder = verdict_tables if spec.by_task else field_table
        out += [*builder(spec, table, entry.get("fields") or {}, missing), ""]
    if opaque_counts:
        out += ["### Other files", "", "| file | count | what it holds |", "|---|---|---|"]
        out += [f"| `{pattern}` | {n:,} | {cell(data['opaque'][pattern])} |"
                for pattern, n in sorted(opaque_counts.items(), key=lambda kv: list(data["opaque"]).index(kv[0]))]
        out.append("")
    if missing:
        raise Undescribed(f"{study.name}: fields without a meaning in {FIELDS_FILE.name}: {', '.join(missing)}")
    out += ["## Links", "", data["links_note"].strip(), "", "| link | resolves |", "|---|---|"]
    out += [f"| {cell(link)} | {n} |" for link, n in link_rows(study, runs)]
    out += ["", "## Ladders", "", data["ladders_note"].strip(), "", *ladder_lines(), "",
            "## Load in Python", "", "```python", about["load"].strip(), "```", ""]
    if about.get("notes"):
        out += ["## Notes", "", about["notes"].strip(), ""]
    return "\n".join(out)


def data_mtime(study: Path) -> float:
    """The newest change to the data a README is rendered from: every file and folder under runs/<study>/ but the
    README, and under logs/<study>/ (the folder tree reads). A folder's time moves when an entry is added to it,
    removed or renamed, so a run moved into a group counts. A smoke suite's folder (smoke*, no run) is left out with
    all it holds: the tree names a suite and counts none of its runs or logs. A path that vanishes during the walk is a
    change now: infinity."""
    study = Path(study)
    runs = runs_of(study)
    kept = {*GROUPS, *(run.relative_to(study).parts[0] for run in runs)}
    logs = study.resolve().parent.parent / "logs" / study.name
    log_kept = {*GROUPS, *meanings()["log_groups"], *(run.name for run in runs if run.parent == study)}

    def suite(path: Path, top: Path) -> bool:
        first = path.relative_to(top).parts[0] if path != top else None
        if first is None:
            return False
        return first.startswith("smoke") and first not in (kept if top == study else log_kept)

    newest = 0.0
    for top in (study, logs):
        if not top.is_dir():
            continue
        for path in (top, *top.rglob("*")):
            if path == study / README or suite(path, top):
                continue
            try:
                newest = max(newest, path.lstat().st_mtime)
            except FileNotFoundError:
                return float("inf")
    return newest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("studies", nargs="*", type=Path, help="runs/<study> folders")
    parser.add_argument("--all", action="store_true", help="every study in STUDIES under runs/")
    parser.add_argument("--check", action="store_true", help="write nothing; exit 1 when a README differs")
    args = parser.parse_args(argv)
    studies = list(args.studies) + ([ROOT / "runs" / s for s in STUDIES if (ROOT / "runs" / s).is_dir()]
                                    if args.all else [])
    if not studies:
        parser.error("name a study or pass --all")
    status, counts = 0, Counter()
    progress.step(f"{'Check' if args.check else 'Write'} the README of "
                  f"{progress.plural(len(studies), 'study', 'studies')}")
    for study in studies:
        text = render(study)
        path = study / README
        current = path.read_text() if path.is_file() else None
        if current == text:
            state = "current"
            if not args.check:
                path.touch()        # its time marks the data it is current against (data_mtime)
        elif args.check:
            state, status = "out of date", 1
        else:
            path.write_text(text)
            state = "written"
        counts[state] += 1
        progress.detail(state, rel(path), err=state == "out of date")
    tally = ", ".join(f"{n} {state}" for state, n in counts.items())
    if status:
        progress.failed(f"{tally}; make study-readmes writes them")
    else:
        progress.done(f"{len(studies)} READMEs: {tally}")
    return status


def rel(path: Path) -> str:
    """`path` from the repository root when under it."""
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


if __name__ == "__main__":
    sys.exit(main())
