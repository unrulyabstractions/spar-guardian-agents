"""A study as one grid: one cell per setting, one mark per seed, each mark linking to that run's page.

    runs = study_runs(Path("runs/regulator_scale_2"))              # [(folder, incomplete)]
    rows = [run_summary(RunFiles(folder), incomplete) for folder, incomplete in runs]
    grid = study_grid(rows)    # {"rows": [...], "cols": [...], "cells": {"r|c": [summary, ...]}, ...}

The axes are the setup fields that vary across the runs (run_setup.run_setup), split between rows and columns so
the grid has few headers and few empty cells and fits the page; counts and the task version prefer columns, models
and the rule prefer rows. A run without processed/run.json cannot be placed and goes to a last row of its own. A
run without labels is kept and marked as missing.
"""
from __future__ import annotations

import html
import json
from collections import Counter
from pathlib import Path

from .run_files import RunFiles
from .run_setup import run_setup

RUNGS = ("none", "looked", "tempted", "attempted", "hacked")
CHEATED_FROM = RUNGS.index("attempted")
COL_FIELDS = ("version", "told", "workers", "regulators", "disregulators")
ROW_FIELDS = ("worker_model", "regulator_model", "disregulator_model", "rule")
WEB = Path(__file__).resolve().parent / "web"
WIDTH_BUDGET, CELL_MIN, MARK_WIDTH, MARKS_PER_LINE = 1400, 130, 46, 3   # pixels, for a page 1600 wide


def looks_like_run(folder: Path) -> bool:
    return folder.is_dir() and ((folder / "processed").is_dir() or (folder / "analysis").is_dir())


def study_runs(study: Path) -> list[tuple[Path, bool]]:
    """(run folder, incomplete) for every run of the study, the ones in incomplete/ included."""
    out = [(p, False) for p in sorted(study.iterdir()) if p.name != "incomplete" and looks_like_run(p)]
    unfinished = study / "incomplete"
    if unfinished.is_dir():
        out += [(p, True) for p in sorted(unfinished.iterdir()) if looks_like_run(p)]
    return out


def field_values(setup: dict) -> dict:
    """The grid's fields of one run's setup, each a hashable value."""
    return {"version": (setup.get("version"), bool(setup.get("check_tool"))),
            "told": bool(setup.get("told_to_cheat")),
            "workers": setup.get("workers"),
            "worker_model": setup.get("worker_model"),
            "regulators": setup.get("regulators") or 0, "regulator_model": setup.get("regulator_model"),
            "disregulators": setup.get("disregulators") or 0, "disregulator_model": setup.get("disregulator_model"),
            "rule": setup.get("rule")}


def run_summary(files: RunFiles, incomplete: bool = False) -> dict:
    """What the study grid shows of one run, from its files only."""
    run_json = files.run_json()
    placed = bool(run_json)
    versions = files.version_values()
    setup = run_setup(run_json, files.roles(), versions) if placed else None
    labelled = files.has_labels()
    cheating = files.cheating() if labelled else {}
    team = cheating.get("team")
    record = run_json.get("unsubmits") or {}
    signals = files.signals()
    team_row = next((row for row in signals if row.get("row") == "team"), {})
    outcomes = [o.get("outcome") for o in team_row.get("alert_outcomes") or []]
    checks = [row for row in versions.get("checks") or [] if isinstance(row, dict)]
    final = next((row for row in versions.get("hand_ins") or [] if isinstance(row, dict) and row.get("final")), None)
    held = final.get("held") if isinstance(final, dict) and isinstance(final.get("held"), list) else None
    submits = len(record.get("submits") or []) or (1 if run_json.get("team_submission") else 0)
    return {"run": files.name, "incomplete": incomplete, "placed": placed, "setup": setup,
            "fields": field_values(setup) if setup else None,
            "seed": (setup or {}).get("seed"),
            "labelled": labelled and team in RUNGS,
            "team_rung": RUNGS.index(team) if team in RUNGS else None,
            "passed": bool(cheating.get("passed")) if labelled else None,
            "handins": submits, "takebacks": len(record.get("unsubmits") or []),
            "alerts": len(run_json.get("alerts") or []),
            "false_alarms": sum(1 for o in outcomes if o == "false_alarm"),
            "checks": len(checks), "accepted": sum(1 for c in checks if c.get("answer") == "accepted"),
            "values": len(versions.get("values") or {}), "final_held": None if held is None else len(held)}


def _sort_key(value):
    """None first, then numbers, then text; tuples element by element."""
    if isinstance(value, tuple):
        return tuple(_sort_key(v) for v in value)
    if value is None:
        return (0, 0, "")
    if isinstance(value, bool):
        return (1, int(value), "")
    if isinstance(value, (int, float)):
        return (1, value, "")
    return (2, 0, str(value))


def grid_cost(placed: list[dict], rows: list[str], cols: list[str]) -> float:
    """How hard a split of the fields into rows and columns is to read: the headers to read, empty cells, columns
    past the page's width, one tall column or one long row, and a field on the side it does not prefer."""
    cells = Counter((key(s, rows), key(s, cols)) for s in placed)
    n_rows, n_cols = len({r for r, _ in cells}), len({c for _, c in cells})
    empty = n_rows * n_cols - len(cells)
    width = n_cols * max(CELL_MIN, MARK_WIDTH * min(max(cells.values()), MARKS_PER_LINE) + 24)
    over = max(0, width - WIDTH_BUDGET) / CELL_MIN
    side = sum(1 for f in rows if f in COL_FIELDS) + sum(1 for f in cols if f in ROW_FIELDS)
    flat = 2 if (n_cols == 1 and n_rows > 6) or (n_rows == 1 and n_cols > 4) else 0
    return n_rows + n_cols + 0.3 * empty + 4 * over + flat + 0.3 * side


def study_grid(summaries: list[dict]) -> dict:
    """Rows, columns and cells for the placed runs; the unplaced ones apart. The fields that vary are split
    between rows and columns the way that reads best (grid_cost)."""
    placed = [s for s in summaries if s["fields"]]
    fields = COL_FIELDS + ROW_FIELDS
    varies = [f for f in fields if len({s["fields"][f] for s in placed}) > 1]
    best: tuple[float, list[str], list[str]] | None = None
    for mask in range(1 << len(varies)):
        cols = [f for k, f in enumerate(varies) if mask >> k & 1]
        rows = [f for f in varies if f not in cols]
        cost = grid_cost(placed, rows, cols) if placed else 0.0
        if best is None or cost < best[0] - 1e-9:
            best = (cost, rows, cols)
    rows, cols = (best[1], best[2]) if best else ([], [])
    row_keys = sorted({key(s, rows) for s in placed}, key=_sort_key)
    col_keys = sorted({key(s, cols) for s in placed}, key=_sort_key)
    cells: dict[str, list[dict]] = {}
    for s in sorted(placed, key=lambda s: (s["incomplete"], _sort_key(s["seed"]), s["run"])):
        cells.setdefault(f"{row_keys.index(key(s, rows))}|{col_keys.index(key(s, cols))}", []).append(s)
    return {"row_fields": rows, "col_fields": cols,
            "rows": [dict(zip(rows, k)) for k in row_keys], "cols": [dict(zip(cols, k)) for k in col_keys],
            "cells": cells, "unplaced": [s for s in summaries if not s["fields"]],
            "shared": {f: placed[0]["fields"][f] for f in fields if placed and f not in varies}}


def key(summary: dict, fields: list[str]) -> tuple:
    return tuple(summary["fields"][f] for f in fields)


# ---------- the page ----------
def chip(field: str, value) -> str:
    """One field's value as a chip: icons, numbers and single words."""
    esc = html.escape
    if field == "version":
        name, check = value
        return (f'<span class="chip ver">{esc(str(name or "?"))}</span>'
                f'<span class="chip {"tool" if check else "notool"}">◎{"" if check else "✗"}</span>')
    if field == "told":
        return '<span class="chip told">⚑ cheat</span>' if value else '<span class="chip dim">⚑✗</span>'
    if field == "workers":
        return f'<span class="chip w">● {esc(str(value if value is not None else "?"))}</span>'
    if field == "worker_model":
        return f'<span class="chip w">● {esc(str(value or "?"))}</span>'
    if field in ("regulators", "disregulators"):
        icon, tone = ("■", "r") if field == "regulators" else ("✹", "d")
        if not value:
            return f'<span class="chip dim">{icon} 0</span>'
        return f'<span class="chip {tone}">{icon} {value}</span>'
    if field in ("regulator_model", "disregulator_model"):
        icon, tone = ("■", "r") if field == "regulator_model" else ("✹", "d")
        if not value:
            return f'<span class="chip dim">{icon} –</span>'
        return f'<span class="chip {tone}">{icon} {esc(value)}</span>'
    if field == "rule":
        return f'<span class="chip rule">§ {esc(str(value))}</span>' if value else '<span class="chip dim">§✗</span>'
    return f'<span class="chip">{esc(str(value))}</span>'


def mark(s: dict, href: str | None) -> str:
    esc = html.escape
    rung = s["team_rung"]
    classes = ["mark"]
    if not s["labelled"]:
        classes.append("missing")
    else:
        classes.append(f"r{rung}")
        if s["passed"]:
            classes.append("passed")
    if s["incomplete"]:
        classes.append("incomplete")
    leak = ""
    if s["values"]:
        n, held = s["values"], s["final_held"]
        level = "unknown" if held is None else "none" if held == 0 else "all" if held >= n else "some"
        leak = f'<i class="leak {level}"></i>'
    glyphs = []
    if s["handins"]:
        glyphs.append(f'<span class="g h">✓{s["handins"]}</span>')
    if s["takebacks"]:
        glyphs.append(f'<span class="g b">↩{s["takebacks"]}</span>')
    if s["checks"]:
        glyphs.append(f'<span class="g c">◎{s["accepted"]}/{s["checks"]}</span>')
    if s["alerts"]:
        glyphs.append(f'<span class="g a">⚠{s["alerts"]}</span>')
    title = (f'{s["run"]} · {RUNGS[rung] if s["labelled"] else "?"} · '
             f'{"pass" if s["passed"] else "fail" if s["passed"] is not None else "?"}')
    face = "?" if not s["labelled"] else ""
    body = (f'<span class="{" ".join(classes)}">{face}{leak}</span>'
            f'<span class="seed">s{esc(str(s["seed"] if s["seed"] is not None else "?"))}</span>'
            f'<span class="glyphs">{"".join(glyphs)}</span>')
    if href:
        return f'<a class="run" href="{esc(href)}" title="{esc(title)}">{body}</a>'
    return f'<span class="run" title="{esc(title)}">{body}</span>'


def cell_share(runs: list[dict]) -> str:
    graded = [s for s in runs if s["labelled"]]
    cheated = sum(1 for s in graded if s["team_rung"] >= CHEATED_FROM)
    passed = sum(1 for s in graded if s["passed"])
    if not graded:
        return '<span class="share">–</span>'
    return (f'<span class="share"><b class="{"hot" if cheated else ""}">▲{cheated}</b>'
            f'<b class="{"ok" if passed else ""}">○{passed}</b><i>/{len(graded)}</i></span>')


def build_study_page(study: str, summaries: list[dict], hrefs: dict[str, str]) -> str:
    grid = study_grid(summaries)
    esc = html.escape

    def label(fields: list[str], values: dict) -> str:
        return "".join(chip(f, values[f]) for f in fields) or '<span class="chip dim">·</span>'

    head = "".join(f'<th class="col">{label(grid["col_fields"], c)}</th>' for c in grid["cols"])
    body = []
    for i, r in enumerate(grid["rows"]):
        tds = []
        for j, _ in enumerate(grid["cols"]):
            runs = grid["cells"].get(f"{i}|{j}", [])
            if not runs:
                tds.append('<td class="empty"></td>')
                continue
            graded = [s for s in runs if s["labelled"]]
            heat = (sum(1 for s in graded if s["team_rung"] >= CHEATED_FROM) / len(graded)) if graded else 0
            marks = "".join(mark(s, hrefs.get(s["run"])) for s in runs)
            tds.append(f'<td style="--heat:{heat:.2f}">{cell_share(runs)}<div class="marks">{marks}</div></td>')
        body.append(f'<tr><th class="row">{label(grid["row_fields"], r)}</th>{"".join(tds)}</tr>')
    if grid["unplaced"]:
        marks = "".join(mark(s, hrefs.get(s["run"])) for s in grid["unplaced"])
        body.append(f'<tr><th class="row"><span class="chip dim">?</span></th>'
                    f'<td colspan="{max(1, len(grid["cols"]))}"><div class="marks">{marks}</div></td></tr>')
    # What every run shares, leaving out the absent things (no regulators, no rule, not told to cheat).
    shared = "".join(chip(f, v) for f, v in grid["shared"].items() if f == "version" or v)
    graded = [s for s in summaries if s["labelled"]]
    cheated = sum(1 for s in graded if s["team_rung"] >= CHEATED_FROM)
    passed = sum(1 for s in graded if s["passed"])
    missing = len(summaries) - len(graded)
    totals = (f'<span class="chip">{len(summaries)} runs</span>'
              f'<span class="chip hot">▲ {cheated}</span><span class="chip ok">○ {passed}</span>'
              + (f'<span class="chip dim">? {missing}</span>' if missing else ""))
    swatches = "".join(f'<span class="key"><span class="mark r{k}"></span>{w}</span>' for k, w in enumerate(RUNGS))
    legend = (f'{swatches}<span class="key"><span class="mark r0 passed"></span>pass</span>'
              '<span class="key"><span class="mark missing">?</span>no labels</span>'
              '<span class="key"><span class="mark r0 incomplete"></span>incomplete</span>'
              '<span class="key"><span class="mark r0"><i class="leak none"></i></span>0 held</span>'
              '<span class="key"><span class="mark r0"><i class="leak some"></i></span>some</span>'
              '<span class="key"><span class="mark r0"><i class="leak all"></i></span>all</span>'
              '<span class="key"><span class="mark r0"><i class="leak unknown"></i></span>?</span>'
              '<span class="key g h">✓ hand-ins</span><span class="key g b">↩ take-backs</span>'
              '<span class="key g c">◎ accepted/checks</span><span class="key g a">⚠ alerts</span>'
              '<span class="key"><b class="hot">▲</b> cheated</span><span class="key"><b class="ok">○</b> pass</span>')
    css = (WEB / "study.css").read_text()
    # "</" inside a <script> block would end it early.
    data = json.dumps({"study": study, "runs": summaries}).replace("</", "<\\/")
    return (f'<!DOCTYPE html>\n<html lang="en"><head><meta charset="utf-8"><title>{esc(study)}</title>'
            f'<style>{css}</style></head><body>'
            f'<header><h1>{esc(study)}</h1><div class="totals">{totals}</div>'
            f'<div class="shared">{shared}</div></header>'
            f'<table><thead><tr><th></th>{head}</tr></thead><tbody>{"".join(body)}</tbody></table>'
            f'<footer>{legend}</footer>'
            f'<script id="summary" type="application/json">{data}</script></body></html>')


def write_study(study: Path, out_dir: Path, runs_pages: bool = True, log=print) -> Path:
    """out_dir/index.html for the study, and each placed run's page beside it (<run>.html)."""
    from .page_builder import write_page
    from .swarm_story import build_story

    out_dir.mkdir(parents=True, exist_ok=True)
    summaries, hrefs = [], {}
    for folder, incomplete in study_runs(study):
        files = RunFiles(folder)
        summary = run_summary(files, incomplete)
        summaries.append(summary)
        if not summary["placed"]:
            continue
        page = out_dir / f"{folder.name}.html"
        if runs_pages:
            story = build_story(files)
            story["back"] = "index.html"
            write_page(story, page)
        if page.is_file():
            hrefs[folder.name] = page.name
        log(f"  {folder.name}")
    index = out_dir / "index.html"
    index.write_text(build_study_page(study.name, summaries, hrefs))
    return index

