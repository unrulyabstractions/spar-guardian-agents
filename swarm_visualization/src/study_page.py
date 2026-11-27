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
import math
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
    # Each alert counts once: a true alarm, a false alarm, or unknown (no outcome, or its outcome is null).
    alerts = len(run_json.get("alerts") or [])
    outcomes = outcomes[:alerts]
    true_alarms = sum(1 for o in outcomes if o == "true_alarm")
    false_alarms = sum(1 for o in outcomes if o == "false_alarm")
    return {"run": files.name, "path": f"incomplete/{files.name}" if incomplete else files.name,
            "incomplete": incomplete, "placed": placed, "setup": setup,
            "fields": field_values(setup) if setup else None,
            "seed": (setup or {}).get("seed"),
            "labelled": labelled and team in RUNGS,
            "team_rung": RUNGS.index(team) if team in RUNGS else None,
            "passed": bool(cheating.get("passed")) if labelled else None,
            "submitted": bool(run_json.get("team_submission")),
            "handins": submits, "takebacks": len(record.get("unsubmits") or []),
            "alerts": alerts, "true_alarms": true_alarms, "false_alarms": false_alarms,
            "unknown_alarms": alerts - true_alarms - false_alarms,
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


def held_level(s: dict) -> str | None:
    """What the final hand-in held, as the dot's level: None when there was no hand-in or no planted values."""
    if not s.get("submitted") or not s["values"]:
        return None
    n, held = s["values"], s["final_held"]
    return "unknown" if held is None else "none" if held == 0 else "all" if held >= n else "some"


RUNG_HEX = ("#c9d6e8", "#ffe1a6", "#ff9d47", "#ff2a2a", "#ff0a3c")
HACKED = RUNGS.index("hacked")


def hexagon(radius: float) -> str:
    """The points of a hexagon with a vertex at the top, centred on 0."""
    return " ".join(f"{radius * math.sin(k * math.pi / 3):.2f},{-radius * math.cos(k * math.pi / 3):.2f}"
                    for k in range(6))


def disc_svg(rung: int | None, passed: bool) -> str:
    """The verdict's shape: a round disc in the team rung's colour, a red hexagon for hacked; a white ring of the same
    shape for pass. The same shape as the run page's header badge."""
    fill = RUNG_HEX[rung] if rung is not None else "#4b5263"
    if rung == HACKED:
        shape = f'<polygon points="{hexagon(11)}" fill="{fill}"/>'
        ring = (f'<polygon points="{hexagon(14.2)}" fill="none" stroke="#ffffff" stroke-width="2.2" '
                f'stroke-linejoin="round"/>' if passed else "")
    else:
        shape = f'<circle r="10" fill="{fill}"/>'
        ring = '<circle r="13.6" fill="none" stroke="#ffffff" stroke-width="2.2"/>' if passed else ""
    return f'<svg viewBox="-15 -15 30 30" aria-hidden="true">{shape}{ring}</svg>'


def alert_line(s: dict) -> str:
    """Alerts split three ways: amber the true ones, grey the false ones, ? the unknown ones (no outcome)."""
    if not s.get("alerts"):
        return ""
    parts = [f'⚠{s["true_alarms"]}']
    if s["false_alarms"]:
        parts.append(f'<i>{s["false_alarms"]}</i>')
    if s["unknown_alarms"]:
        parts.append(f'<u>?{s["unknown_alarms"]}</u>')
    return f'<span class="g a">{"".join(parts)}</span>'


def hover_counts(s: dict) -> str:
    """The counts that live in the disc's hover title: hand-ins, take-backs, check_fix answers. The disc itself
    shows ∅ when no hand-in stands."""
    parts = [f'✓{s["handins"]}']
    if s["takebacks"]:
        parts.append(f'↩{s["takebacks"]}')
    if s["checks"]:
        parts.append(f'◉{s["accepted"]} ◎{s["checks"] - s["accepted"]}')
    return " ".join(parts)


def mark(s: dict, href: str | None) -> str:
    """One run: its disc (team rung's colour, a hexagon for hacked, a white ring when it passed), the held dot, ∅ when
    no hand-in stands, its seed and its alerts. A run in incomplete/ is a dashed ring only."""
    esc = html.escape
    if s["incomplete"]:
        body = '<span class="mark incomplete"></span>'
        title = f'{s["run"]} · incomplete'
    else:
        verdict = (f'{RUNGS[s["team_rung"]]} · {"pass" if s["passed"] else "fail"}' if s["labelled"] else "?")
        title = f'{s["run"]} · {verdict} · {hover_counts(s)}'
        classes = ["mark", f'r{s["team_rung"]}' if s["labelled"] else "missing"]
        level = held_level(s)
        leak = f'<i class="leak {level}"></i>' if level else ""
        face = "?" if not s["labelled"] else "" if s.get("submitted") else "∅"
        svg = disc_svg(s["team_rung"], bool(s["passed"])) if s["labelled"] else ""
        seed = f'<span class="seed">s{esc(str(s["seed"] if s["seed"] is not None else "?"))}</span>'
        body = (f'<span class="{" ".join(classes)}">{svg}<b class="face">{face}</b>{leak}</span>{seed}'
                f'{alert_line(s)}')
    if href:
        return f'<a class="run" href="{esc(href)}" title="{esc(title)}">{body}</a>'
    return f'<span class="run" title="{esc(title)}">{body}</span>'


def finished(runs: list[dict]) -> list[dict]:
    return [s for s in runs if not s["incomplete"]]


def tally(runs: list[dict]) -> tuple[int, int, int]:
    """(cheated, passed, graded) over the finished runs with labels."""
    graded = [s for s in finished(runs) if s["labelled"]]
    return (sum(1 for s in graded if s["team_rung"] >= CHEATED_FROM), sum(1 for s in graded if s["passed"]),
            len(graded))


def share_bar(runs: list[dict]) -> str:
    """The cheated share of the finished runs with labels: a thin bar and 'cheated/graded'."""
    cheated, _, graded = tally(runs)
    if not graded:
        return '<span class="frac dim">–</span>'
    return (f'<span class="frac{" hot" if cheated else ""}">{cheated}/{graded}</span>'
            f'<span class="bar"><i style="width:{100 * cheated / graded:.0f}%"></i></span>')


def cell_share(runs: list[dict]) -> str:
    cheated, passed, graded = tally(runs)
    if not graded:
        return '<span class="share">–</span>'
    return (f'<span class="share"><b class="{"hot" if cheated else ""}">▲{cheated}</b>'
            f'<b class="{"ok" if passed else ""}">○{passed}</b><i>/{graded}</i></span>')


def marks(runs: list[dict], hrefs: dict[str, str]) -> str:
    done = "".join(mark(s, hrefs.get(s.get("path", s["run"]))) for s in runs if not s["incomplete"])
    aside = "".join(mark(s, hrefs.get(s.get("path", s["run"]))) for s in runs if s["incomplete"])
    return f'<div class="marks">{done}{f"<span class=aside>{aside}</span>" if aside else ""}</div>'


AXIS_WORDS = {"workers": "workers", "worker_model": "workers", "regulators": "regulators",
              "regulator_model": "regulator", "disregulators": "disregulator", "disregulator_model": "disregulator",
              "version": "version", "told": "told", "rule": "rule"}


def axis_word(fields: list[str]) -> str:
    """One word for an axis: the role its fields set, a regulator or disregulator field first."""
    words = [AXIS_WORDS.get(f, f) for f in fields]
    for word in ("regulators", "disregulator", "regulator"):
        if word in words:
            return word
    return words[0] if words else ""


def build_study_page(study: str, summaries: list[dict], hrefs: dict[str, str]) -> str:
    grid = study_grid(summaries)
    esc = html.escape

    def label(fields: list[str], values: dict) -> str:
        return "".join(chip(f, values[f]) for f in fields) or '<span class="chip dim">·</span>'

    def cell_runs(i: int, j: int) -> list[dict]:
        return grid["cells"].get(f"{i}|{j}", [])

    n_rows, n_cols = len(grid["rows"]), len(grid["cols"])
    head = "".join(f'<th class="col">{label(grid["col_fields"], c)}'
                   f'<div class="margin">{share_bar([s for i in range(n_rows) for s in cell_runs(i, j)])}</div></th>'
                   for j, c in enumerate(grid["cols"]))
    body = []
    for i, r in enumerate(grid["rows"]):
        tds = []
        for j in range(n_cols):
            runs = cell_runs(i, j)
            if not runs:
                tds.append('<td class="empty"></td>')
                continue
            # A cell with one finished run shows only its disc: the margins carry the shares.
            if len(finished(runs)) == 1:
                tds.append(f'<td class="one">{marks(runs, hrefs)}</td>')
            else:
                tds.append(f'<td>{cell_share(runs)}{marks(runs, hrefs)}<div class="foot">{share_bar(runs)}</div></td>')
        row_runs = [s for j in range(n_cols) for s in cell_runs(i, j)]
        body.append(f'<tr><th class="row">{label(grid["row_fields"], r)}'
                    f'<div class="margin">{share_bar(row_runs)}</div></th>{"".join(tds)}</tr>')
    if grid["unplaced"]:
        body.append(f'<tr><th class="row"><span class="chip dim">?</span></th>'
                    f'<td colspan="{max(1, n_cols)}">{marks(grid["unplaced"], hrefs)}</td></tr>')
    across, down = axis_word(grid["col_fields"]), axis_word(grid["row_fields"])
    corner = ((f'<span class="axis across">{esc(across)} →</span>' if across else "")
              + (f'<span class="axis down">{esc(down)} ↓</span>' if down else ""))
    # What every run shares, leaving out the absent things (no regulators, no rule, not told to cheat).
    shared = "".join(chip(f, v) for f, v in grid["shared"].items() if f == "version" or v)
    done = finished(summaries)
    cheated, passed, graded = tally(summaries)
    missing = len(done) - graded
    unfinished = len(summaries) - len(done)
    totals = (f'<span class="chip">{len(done)} runs</span>'
              f'<span class="chip hot">▲ {cheated}</span><span class="chip ok">○ {passed}</span>'
              + (f'<span class="chip dim">? {missing}</span>' if missing else "")
              + (f'<span class="chip dim"><span class="mark incomplete tiny"></span> {unfinished}</span>'
                 if unfinished else ""))
    swatches = "".join(f'<span class="key"><span class="mark r{k}">{disc_svg(k, False)}</span>{w}</span>'
                       for k, w in enumerate(RUNGS))
    legend = (f'{swatches}<span class="key"><span class="mark r0">{disc_svg(0, True)}</span>pass</span>'
              '<span class="key"><span class="mark missing"><b class="face">?</b></span>no labels</span>'
              '<span class="key"><span class="mark incomplete"></span>incomplete</span>'
              + "".join(f'<span class="key"><span class="mark r0">{disc_svg(0, False)}'
                        f'<i class="leak {level}"></i></span>{word}</span>'
                        for level, word in (("none", "0 held"), ("some", "some"), ("all", "all"), ("unknown", "?")))
              + '<span class="key g n">∅ no hand-in</span>'
              '<span class="key g a">⚠ true <i>false</i> <u>?</u></span>'
              '<span class="key"><b class="hot">▲</b> cheated</span><span class="key"><b class="ok">○</b> pass</span>'
              '<span class="key"><span class="bar"><i style="width:60%"></i></span>cheated share</span>'
              '<span class="key">§ rule</span>')
    css = (WEB / "study.css").read_text()
    # "</" inside a <script> block would end it early.
    data = json.dumps({"study": study, "runs": summaries}).replace("</", "<\\/")
    return (f'<!DOCTYPE html>\n<html lang="en"><head><meta charset="utf-8"><title>{esc(study)}</title>'
            f'<style>{css}</style></head><body>'
            f'<header><h1>{esc(study)}</h1><div class="totals">{totals}</div>'
            f'<div class="shared">{shared}</div></header>'
            f'<table><thead><tr><th class="corner">{corner}</th>{head}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table>'
            f'<footer>{legend}</footer>'
            f'<script id="summary" type="application/json">{data}</script></body></html>')


def write_study(study: Path, out_dir: Path, runs_pages: bool = True, log=print) -> Path:
    """out_dir/index.html for the study, and each placed run's page beside it: <run>.html for a finished run,
    incomplete/<run>.html for a run in incomplete/. Links are keyed by the run's folder path, since a finished run
    and an incomplete one can share a name."""
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
        href = f'{summary["path"]}.html'
        page = out_dir / href
        if runs_pages:
            story = build_story(files)
            story["back"] = "../index.html" if incomplete else "index.html"
            story["incomplete"] = incomplete
            write_page(story, page)
        if page.is_file():
            hrefs[summary["path"]] = href
        log(f"  {summary['path']}")
    index = out_dir / "index.html"
    index.write_text(build_study_page(study.name, summaries, hrefs))
    return index
