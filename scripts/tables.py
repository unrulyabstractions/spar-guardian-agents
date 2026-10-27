"""The paper's configuration tables, as PNG under tmp/tables/ (gitignored), every value read from the source.

    uv run python scripts/tables.py

Tables: scales_summary (agents, time limit, concurrency, model calls per scale), turn_budget (per agent, with the lone
runs behind it), scales (every field of each scale), preset (the default preset against the field defaults),
config_fields (every SwarmConfig field: type, default, first sentence of its docstring), modes, levels_<ladder> (one
per instruction ladder) and levels_vars, controls and control_texts, models and model_routes. Sources:
src/configuration/swarm_config.py (parsed with ast, and imported), scales.py, settings.py, instruction_levels.yaml,
controls.yaml, src/swarm/controls.py (parsed) and models.yaml. The script refuses to write when a field, a level or a
model is missing from its table.
"""
from __future__ import annotations

import ast
import json
import re
import sys
import textwrap
from pathlib import Path

import matplotlib
import yaml
from matplotlib.figure import Figure

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.configuration import settings  # noqa: E402
from src.configuration.scales import SCALES  # noqa: E402
from src.configuration.swarm_config import DOMAIN_MODES, PRESETS, SwarmConfig  # noqa: E402

matplotlib.use("Agg")
CONF = ROOT / "src" / "configuration"
OUT = ROOT / "tmp" / "tables"
LONE_PREFIX = {"openmath": "om", "swe": "swe", "knowledge_work": "kw"}
DOMAIN_LABEL = {"openmath": "openmath", "swe": "SWE", "knowledge_work": "knowledge work"}
# The renderer: 8-point sans (7.5-point mono), 13 characters to the inch, 0.17 inch per line.
CHARS_PER_INCH = 13
LINE = 0.17
PAD = 0.08
INK = "#0b0b0b"
RULE = "#c3c2b7"
HEAD = "#f2f1ec"


def wrap(text: str, width_in: float, mono: bool = False) -> list[str]:
    """The cell's text as lines that fit its column (monospace runs 11 characters to the inch)."""
    chars = max(6, int(width_in * (11 if mono else CHARS_PER_INCH)))
    lines = []
    for paragraph in str(text).split("\n"):
        lines += textwrap.wrap(paragraph, chars, break_long_words=True, break_on_hyphens=False) or [""]
    return lines


def table_png(name: str, header: list[str], rows: list[list[str]], widths: list[float], note: str | None = None,
              mono: tuple[int, ...] = ()) -> Path:
    """One table as a PNG: a bold header row, wrapped cells, a note below. widths are column widths in inches."""
    cells = [[wrap(cell, w - 2 * PAD, r > 0 and c in mono) for c, (cell, w) in enumerate(zip(row, widths))]
             for r, row in enumerate([header, *rows])]
    heights = [max(len(c) for c in row) * LINE + 2 * PAD for row in cells]
    width = sum(widths)
    note_lines = wrap(note, width - 2 * PAD) if note else []
    height = sum(heights) + (len(note_lines) * LINE + 2 * PAD if note_lines else 0) + 0.1
    fig = Figure(figsize=(width, height), dpi=200)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, width)
    ax.set_ylim(height, 0)
    ax.axis("off")
    y = 0.05
    for r, (row, h) in enumerate(zip(cells, heights)):
        x = 0.0
        for c, (lines, w) in enumerate(zip(row, widths)):
            ax.add_patch(matplotlib.patches.Rectangle((x, y), w, h, facecolor=HEAD if r == 0 else "#ffffff",
                                                      edgecolor=RULE, linewidth=0.6))
            is_mono = r > 0 and c in mono
            ax.text(x + PAD, y + PAD, "\n".join(lines), fontsize=7.5 if is_mono else 8,
                    family="DejaVu Sans Mono" if is_mono else "DejaVu Sans", color=INK,
                    weight="bold" if r == 0 else "normal", va="top", ha="left", linespacing=1.25)
            x += w
        y += h
    if note_lines:
        ax.text(PAD, y + PAD, "\n".join(note_lines), fontsize=7.5, color="#52514e", style="italic", va="top",
                ha="left", linespacing=1.25)
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{name}.png"
    fig.savefig(path, dpi=200, facecolor="#ffffff")
    return path


def compact(value) -> str:
    """A field value in a few characters: windows as start:end, dicts as key: value pairs, big ints with commas."""
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    if isinstance(value, dict) and set(value) == {"start", "end"}:
        return f"{value['start']}:{'' if value['end'] is None else value['end']}"
    if isinstance(value, dict):
        return "; ".join(f"{k}: {compact(v)}" for k, v in value.items())
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "none"
    if isinstance(value, int) and abs(value) >= 1000:
        return f"{value:,}"
    return str(value)


def hours(seconds: int) -> str:
    return f"{seconds // 60} min" if seconds < 3600 else f"{seconds // 3600} h"


def first_sentence(text: str) -> str:
    flat = " ".join(text.split())
    return re.split(r"(?<=[.!?])\s+(?=\S)", flat, maxsplit=1)[0]


def config_fields() -> list[dict]:
    """Every annotated field of SwarmConfig in source order: name, type, default as written, docstring."""
    src = (CONF / "swarm_config.py").read_text()
    cls = next(n for n in ast.parse(src).body if isinstance(n, ast.ClassDef) and n.name == "SwarmConfig")
    fields = []
    for i, node in enumerate(cls.body):
        if not (isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)):
            continue
        if node.target.id == "model_config":
            continue
        doc = ""
        after = cls.body[i + 1] if i + 1 < len(cls.body) else None
        if isinstance(after, ast.Expr) and isinstance(after.value, ast.Constant) and isinstance(after.value.value, str):
            doc = after.value.value
        fields.append({"name": node.target.id, "type": ast.unparse(node.annotation),
                       "default": ast.unparse(node.value) if node.value is not None else "(required)",
                       "doc": " ".join(doc.split())})
    return fields


def lone_turns(domain: str) -> list[int]:
    turns = []
    for k in (1, 2, 3):
        episodes = ROOT / "runs" / f"{LONE_PREFIX[domain]}_lone_{k}" / "episodes.jsonl"
        if episodes.is_file():
            turns += [int(json.loads(line)["n_turns"]) for line in episodes.read_text().splitlines() if line.strip()]
    return turns


def write_scales_summary() -> Path:
    rows = [[name.replace("_", " "), str(s.n_agents), hours(s.time_limit_s), str(s.max_concurrency),
             "batch" if s.batch_agents else "live"] for name, s in SCALES.items()]
    return table_png("scales_summary", ["scale", "agents", "time limit", "concurrency", "model calls"], rows,
                     [1.3, 0.9, 1.1, 1.2, 1.2])


def write_turn_budget() -> Path:
    rows = [[DOMAIN_LABEL[d], str(b), ", ".join(map(str, lone_turns(d))) or "(no lone runs on disk)"]
            for d, b in settings.TURN_BUDGETS.items()]
    return table_png("turn_budget", ["domain", "turn budget per agent", "lone runs (turns)"], rows, [1.7, 2.0, 1.9],
                     note="The median of three runs of one agent alone on the task, on the agents' model; every agent "
                          "of a swarm gets that many working steps at every scale.")


SCALE_FIELDS = [("n_agents", "agents"), ("steps_per_agent", "working steps per agent without a turn budget"),
                ("max_message_chars", "longest post (chars)"), ("read_page", "posts per board_read"),
                ("reads_per_turn", "posts read per round"), ("posts_per_turn", "posts written per round"),
                ("free_board_actions", "free board actions per round (K)"), ("time_limit_s", "time limit (s)"),
                ("max_concurrency", "agents at once"), ("batch_agents", "batch API for the agents' calls"),
                ("max_context_chars", "auto-delivery context (chars), derived"),
                ("message_limit", "message limit, derived")]


def write_scales() -> Path:
    data = {name: s.fields() for name, s in SCALES.items()}
    rows = [[f"{label} ({key})"] + [compact(data[n][key]) for n in data] for key, label in SCALE_FIELDS]
    return table_png("scales", ["parameter"] + [n.replace("_", " ") for n in data], rows, [3.0] + [0.95] * len(data),
                     note="Under a turn budget the working steps per agent are the budget, the same at every scale.")


def write_preset() -> Path:
    preset, base = PRESETS["default"].model_dump(), SwarmConfig().model_dump()
    diff = [k for k in SwarmConfig.model_fields if repr(preset.get(k)) != repr(base.get(k))]
    rows = [[k, compact(preset[k]), compact(base[k])] for k in diff]
    return table_png("preset", ["field", "default preset", "field default"], rows, [1.8, 2.4, 1.6], mono=(0,),
                     note=f"The default preset is the small scale with the turn budgets of settings.TURN_BUDGETS: "
                          f"the {len(diff)} fields it sets away from the field defaults.")


def write_config_fields() -> Path:
    fields = config_fields()
    missing = [f["name"] for f in fields if not f["doc"]]
    if missing:
        raise SystemExit(f"fields without a docstring: {missing}")
    rows = [[f["name"], f["type"], f["default"], first_sentence(f["doc"])] for f in fields]
    return table_png("config_fields", ["field", "type", "default", "what it is"], rows, [2.7, 1.8, 1.7, 4.4],
                     mono=(0, 1, 2),
                     note=f"{len(fields)} fields in source order; the first sentence of each docstring.")


def write_modes() -> Path:
    def default_for(mode):
        names = [d for d, m in DOMAIN_MODES.items() if m == mode]
        return ", ".join(names) if names else "none"
    rows = [["solo_task", "each agent its own copy of the task and workspace, with the whole exploit",
             "no (unless mention_team)", "no", default_for("solo_task")],
            ["shared_task", "one task and one deliverable; each agent its own copy of the repository and workspace, a "
             "team remote holds main, and the neutral agents hold different parts of the exploit", "yes",
             "yes, neutral agents only; ends the run", default_for("shared_task")],
            ["collaboration_needed", "one issue split into parts, one or more agents per part; the team's main is "
             "graded (SWE only)", "yes", "yes, neutral agents only; grades and ends the run",
             default_for("collaboration_needed")]]
    return table_png("modes", ["mode", "task", "roster known", "team_submit", "default for"], rows,
                     [1.6, 3.2, 1.2, 1.7, 1.5], mono=(0,),
                     note="A lone agent has no board and no team tools in any mode.")


def write_levels() -> list[Path]:
    data = yaml.safe_load((CONF / "instruction_levels.yaml").read_text())
    benches = list(data.get("vars") or {})
    paths = []
    for ladder, lad in data.items():
        if ladder == "vars" or not isinstance(lad, dict) or "order" not in lad:
            continue
        per_bench = [b for b in benches if any(isinstance(lad.get(lv), dict) and b in lad[lv] for lv in lad["order"])]
        rows = []
        for level in lad["order"]:
            entry = lad.get(level) or {}
            if per_bench and not any(b in entry for b in per_bench):
                rows.append([level, entry.get("text") or "(no sentence)"] + ["(same)"] * (len(per_bench) - 1))
            elif per_bench:
                rows.append([level] + [entry.get(b) or "(none)" for b in per_bench])
            else:
                rows.append([level, entry.get("text") or "(no sentence)"])
        header = ["level"] + (per_bench or ["text"])
        widths = [1.2] + ([7.0 / len(per_bench)] * len(per_bench) if per_bench else [7.0])
        paths.append(table_png(f"levels_{ladder}", header, rows, widths, mono=(0,)))
    rows = [[f"{bench}.{key}", str(value)] for bench, values in (data.get("vars") or {}).items()
            for key, value in values.items()]
    paths.append(table_png("levels_vars", ["placeholder", "text"], rows, [2.0, 6.2], mono=(0,),
                           note="The placeholders each benchmark fills into the levels' sentences."))
    return paths


def controls_catalog() -> dict[str, tuple[list[str], list[str]]]:
    """src/swarm/controls.py _catalog: each named control's ORBIT defense types and the controls.yaml entries it
    reads."""
    tree = ast.parse((ROOT / "src" / "swarm" / "controls.py").read_text())
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_catalog")
    aliases: dict[str, str] = {}
    for node in ast.walk(fn):
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)):
            continue
        src = ast.unparse(node.value)
        keys = re.findall(r"\[['\"](\w+)['\"]\]", src)
        if "load_controls" in src:
            aliases[node.targets[0].id] = ".".join(keys)
        elif isinstance(node.value, ast.Subscript) and keys:
            base = src.split("[")[0]
            aliases[node.targets[0].id] = ".".join(([aliases[base]] if aliases.get(base) else []) + keys)
    ret = next(n for n in ast.walk(fn) if isinstance(n, ast.Return))
    out = {}
    for key, value in zip(ret.value.keys, ret.value.values):
        name = ast.literal_eval(key)
        types, uses = [], []
        for call in ast.walk(value):
            if not isinstance(call, ast.Call):
                continue
            for kw in call.keywords:
                if kw.arg == "defense_type" and isinstance(kw.value, ast.Constant):
                    types.append(kw.value.value)
            if ast.unparse(call.func) == "_llm" and len(call.args) >= 2 and isinstance(call.args[1], ast.Constant):
                types.append(call.args[1].value)
        for node in ast.walk(value):
            if isinstance(node, ast.Name) and aliases.get(node.id):
                uses.append(aliases[node.id])
            if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id in aliases:
                keys = re.findall(r"\[['\"](\w+)['\"]\]", ast.unparse(node))
                uses.append(".".join(([aliases[node.value.id]] if aliases[node.value.id] else []) + keys))
        out[name] = (sorted(set(types)), sorted({u for u in uses if "." in u}))
    return out


def write_controls() -> list[Path]:
    data = yaml.safe_load((CONF / "controls.yaml").read_text()) or {}

    def describe(entry: str) -> str:
        node = data
        for part in entry.split("."):
            node = node.get(part) if isinstance(node, dict) else None
        if isinstance(node, list):
            return f"{len(node)} regex patterns ({entry})"
        if isinstance(node, str):
            return f"criteria, {len(node.split())} words ({entry})"
        return f"{entry} (not in controls.yaml)"

    rows = [[name, ", ".join(types) or "?", "; ".join(describe(u) for u in uses) or "stock: ORBIT's own criteria"]
            for name, (types, uses) in controls_catalog().items()]
    named = table_png("controls", ["control", "ORBIT defense type", "task text"], rows, [1.6, 2.0, 4.0], mono=(0, 1),
                      note="baseline runs no control.")
    rows = [[f"{section}.{key}", f"{len(value)} regex patterns" if isinstance(value, list) else str(value)]
            for section, entries in data.items() for key, value in (entries or {}).items()]
    texts = table_png("control_texts", ["entry", "text"], rows, [2.0, 6.4], mono=(0,))
    return [named, texts]


def write_models() -> list[Path]:
    data = yaml.safe_load((CONF / "models.yaml").read_text())
    key_of = {name: " or ".join(route["key_env"]) for name, route in data["providers"].items()}
    rows = [[name, e["inspect"], key_of[e["provider"]], f"{e['context']:,}", f"{e['usd_per_million']['input']:g}",
             f"{e['usd_per_million']['output']:g}", ", ".join(e["cheatbench"]) or "none", e["status"]]
            for name, e in data["models"].items()]
    models = table_png("models", ["entry", "Inspect model", "key variable", "context", "in", "out", "CheatBench",
                                  "status"], rows, [1.9, 2.3, 1.6, 0.8, 0.6, 0.6, 1.6, 0.9], mono=(0, 1, 2),
                       note=f"List prices in USD per million tokens on {data['as_of']}; status default marks the "
                            "agents' default model and judge the judge's.")
    rows = [[name, r["inspect_prefix"], " or ".join(r["key_env"]), r["base_url_env"], r.get("base_url") or "Inspect's"]
            for name, r in data["providers"].items()]
    routes = table_png("model_routes", ["route", "Inspect prefix", "key variable", "base URL variable", "base URL"],
                       rows, [1.4, 1.5, 2.2, 2.0, 2.4], mono=(1, 2, 3, 4))
    return [models, routes]


def check_complete() -> None:
    """Refuse when a field, a level or a model would be missing from its table."""
    fields = {f["name"] for f in config_fields()}
    missing = set(SwarmConfig.model_fields) - fields
    if missing:
        raise SystemExit(f"config fields not parsed from the source: {sorted(missing)}")
    levels = yaml.safe_load((CONF / "instruction_levels.yaml").read_text())
    for ladder, lad in levels.items():
        if ladder != "vars" and isinstance(lad, dict):
            extra = [k for k in lad if k != "order" and k not in lad["order"]]
            if extra:
                raise SystemExit(f"instruction_levels.{ladder}: levels outside order: {extra}")


def write_tables() -> list[Path]:
    check_complete()
    return [write_scales_summary(), write_turn_budget(), write_scales(), write_preset(), write_config_fields(),
            write_modes(), *write_levels(), *write_controls(), *write_models()]


if __name__ == "__main__":
    for path in write_tables():
        print(path)
