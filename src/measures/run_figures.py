"""Static figures of one run, for the examples and `python -m src plots`: every agent's steps round by round, the
board's actions per round, and the judge's verdicts. Each figure is a PNG with a CSV twin of its data, written under
out/<run name>/ by default (gitignored).

    plot_run(Path("runs/kw_small"), Path("out/kw_small"))      # -> [timeline.png, board.png, verdicts.png]

The data comes from the run directory: run.json (rules.timeline, the mode, the access policy, the model),
episodes.jsonl (each agent's role) and judge.jsonl (each agent's verdict). A run without a timeline gets no timeline
figure, and a run whose agents never touched the board gets no board figure. The colours and the chrome are
src/utils/figure_style.py's: three categorical slots for the step kinds, the status colours for the verdicts; the
judge's flags per task are src/measures/verdict_fields.py's.
"""
from __future__ import annotations

import csv
from pathlib import Path

from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from matplotlib.ticker import MaxNLocator

from src.measures.verdict_fields import FLAGS_BY_TASK
from src.swarm import turn_rules as rules
from src.utils.figure_style import (
    CATEGORICAL,
    CRITICAL,
    DPI,
    GOOD,
    GRID,
    INK,
    INK_SECONDARY,
    MUTED,
    SURFACE,
    WARNING,
    WHITE,
    legend_below,
    style_axes,
    title_axes,
)
from src.utils.json_files import read_json, read_jsonl

KINDS = {"work": ("work step", CATEGORICAL[0]), "board": ("board action", CATEGORICAL[1]),
         "submit": ("submit", CATEGORICAL[2])}
BOARD_ACTIONS = {"board_status": "status", "board_read": "read", "board_post": "post"}
ACTIONS = ("status", "read", "post")
DARK_INK_FILLS = {WARNING}                           # a status fill light enough for ink text
LEGEND = legend_below(-0.22)                         # below the plot, where no title or lane can collide with it


def load_run(run_dir: Path) -> dict:
    """run.json, each agent's role from episodes.jsonl and each agent's verdict from judge.jsonl."""
    run = read_json(run_dir / "run.json")
    agents = run["agents"]
    roles = {e["orbit_agent"]: e.get("role") for e in read_jsonl(run_dir / "episodes.jsonl")}
    verdicts = {}
    for row in read_jsonl(run_dir / "judge.jsonl"):
        agent = row["episode"].split("@", 1)[1] if "@" in row["episode"] else agents[0]
        verdicts[agent] = row
    return {"name": run_dir.name, "run": run, "agents": agents, "roles": roles, "verdicts": verdicts,
            "timeline": (run.get("rules") or {}).get("timeline") or []}


def kind_of(entry: dict) -> str:
    """submit for a step that submitted (alone or for the team), board for a free step or one whose tools were
    board actions, work otherwise (capped steps included)."""
    tools = entry.get("tools") or []
    if entry.get("charge") == "submit" or entry.get("ended") == "submitted" or "team_submit" in tools:
        return "submit"
    if entry.get("charge") == "free" or any(t in BOARD_ACTIONS for t in tools):
        return "board"
    return "work"


def position_of(entry: dict) -> float:
    """The step's place on the round axis, read with the turn rules' own parsers of a step id (r3, r3-a01,
    r9-a01-v4, r3-free1): round k at k, its free follow-ups at k + 0.2, k + 0.4, ..."""
    step = entry.get("step") or ""
    return rules.step_round(step) + 0.2 * rules.free_index(step)


def subtitle(data: dict) -> str:
    run = data["run"]
    policy = (run.get("access") or {}).get("policy")
    parts = [run.get("task"), f"{len(data['agents'])} agent(s)", run.get("mode"),
             f"access {policy}" if policy else None, run.get("model")]
    return " · ".join(str(p) for p in parts if p)


def new_axes(width: float, height: float, title: str, data: dict, grid: str | None = None):
    """A figure with one styled axes, titled with the run's subtitle under the title."""
    fig = Figure(figsize=(width, height), dpi=DPI, facecolor=SURFACE)
    ax = fig.subplots()
    style_axes(ax, grid=grid)
    title_axes(ax, f"{data['name']}: {title}", subtitle(data))
    return fig, ax


def plot_timeline(data: dict, out: Path) -> Path | None:
    """One lane per agent, a mark per step at its round, coloured by kind, the judge's verdict at the lane's end."""
    timeline = data["timeline"]
    if not timeline:
        return None
    agents = data["agents"]
    rounds = max(position_of(e) for e in timeline)
    fig, ax = new_axes(min(16, 7 + 0.25 * rounds), 1.6 + 0.55 * len(agents), "each agent's steps, round by round", data)
    lanes = {a: len(agents) - i for i, a in enumerate(agents)}
    for r in range(1, int(rounds) + 1):
        ax.axvline(r, color=GRID, linewidth=1, zorder=0)
    for entry in timeline:
        kind = kind_of(entry)
        ax.plot(position_of(entry), lanes[entry["agent"]], marker="o", markersize=7, color=KINDS[kind][1],
                markeredgecolor=SURFACE, markeredgewidth=1.5, linestyle="none", zorder=3)
    for agent, lane in lanes.items():
        verdict = data["verdicts"].get(agent)
        if verdict is None or verdict.get("judge_error") or verdict.get("cheated") is None:
            text, colour = "not judged", MUTED
        else:
            text, colour = ("cheated", CRITICAL) if verdict["cheated"] else ("no cheating", GOOD)
        ax.plot(rounds + 0.9, lane, marker="o", markersize=5, color=colour, linestyle="none", clip_on=False)
        ax.text(rounds + 1.15, lane, text, color=INK_SECONDARY, fontsize=8, va="center", clip_on=False)
    ax.set_yticks(list(lanes.values()))
    ax.set_yticklabels([f"{a} · {data['roles'].get(a) or 'agent'}" for a in agents], color=INK)
    ax.set_ylim(0.4, len(agents) + 0.6)
    ax.set_xlim(0.4 if any(position_of(e) < 1 for e in timeline) else 0.6, rounds + 2.6)
    ax.set_xticks(range(1, int(rounds) + 1))
    ax.set_xlabel("round", color=INK_SECONDARY, fontsize=9)
    ax.legend(handles=[Line2D([], [], marker="o", color=c, linestyle="none", markersize=7, label=label)
                       for label, c in KINDS.values()], **LEGEND)
    fig.savefig(out, bbox_inches="tight", facecolor=SURFACE)
    with (out.with_suffix(".csv")).open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["agent", "role", "round", "step", "kind", "tools", "ended"])
        for entry in timeline:
            writer.writerow([entry["agent"], data["roles"].get(entry["agent"]) or "", position_of(entry),
                             entry.get("step"), kind_of(entry), " ".join(entry.get("tools") or []), entry.get("ended")])
    return out


def board_counts(timeline: list[dict]) -> dict[int, dict[str, int]]:
    """{round: {status, read, post}}, counting every board tool call of the round's steps and follow-ups."""
    counts: dict[int, dict[str, int]] = {}
    for entry in timeline:
        round_number = int(position_of(entry))
        for tool in entry.get("tools") or []:
            if tool in BOARD_ACTIONS:
                counts.setdefault(round_number, dict.fromkeys(ACTIONS, 0))[BOARD_ACTIONS[tool]] += 1
    return counts


def plot_board(data: dict, out: Path) -> Path | None:
    """Stacked columns of the board's actions per round: status checks, reads and posts."""
    counts = board_counts(data["timeline"])
    if not counts:
        return None
    rounds = range(1, max(counts) + 1)
    slots = max(len(rounds), 12)                     # at least twelve slots across, so a column stays thin
    fig, ax = new_axes(min(16, 7 + 0.25 * slots), 3.4, "board actions per round", data, grid="y")
    bottom = [0] * len(rounds)
    for action, colour in zip(ACTIONS, [c for _, c in KINDS.values()]):
        values = [counts.get(r, {}).get(action, 0) for r in rounds]
        ax.bar(list(rounds), values, bottom=bottom, width=0.4, color=colour, edgecolor=SURFACE, linewidth=1.5,
               label=action)
        bottom = [b + v for b, v in zip(bottom, values)]
    ax.set_xlim(0.5, slots + 0.5)
    ax.set_xticks(list(rounds))
    ax.set_xlabel("round", color=INK_SECONDARY, fontsize=9)
    ax.set_ylabel("board actions", color=INK_SECONDARY, fontsize=9)
    ax.yaxis.set_major_locator(MaxNLocator(integer=True))
    ax.legend(**LEGEND)
    fig.savefig(out, bbox_inches="tight", facecolor=SURFACE)
    with (out.with_suffix(".csv")).open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["round", *ACTIONS])
        for r in rounds:
            writer.writerow([r, *(counts.get(r, {}).get(a, 0) for a in ACTIONS)])
    return out


def plot_verdicts(data: dict, out: Path) -> Path | None:
    """A grid of the judge's flags per agent: a status tile with yes or no in it, an empty tile for a missing
    verdict."""
    agents = data["agents"]
    if not data["verdicts"]:
        return None
    flags = FLAGS_BY_TASK[data["run"]["task"]]
    fig, ax = new_axes(2.2 + 1.4 * len(flags), 1.6 + 0.5 * len(agents), "the judge's verdict per agent", data)
    for i, agent in enumerate(agents):
        verdict = data["verdicts"].get(agent) or {}
        for j, (flag, _, colour) in enumerate(flags):
            value = None if verdict.get("judge_error") else verdict.get(flag)
            fill = colour if value else SURFACE
            ax.bar(j, 0.8, bottom=len(agents) - 1 - i - 0.4, width=0.9, color=fill,
                   edgecolor=SURFACE if value else GRID, linewidth=1.5)
            text = "yes" if value else ("no" if value is False else "—")
            ax.text(j, len(agents) - 1 - i, text, ha="center", va="center", fontsize=9,
                    color=(INK if (not value or colour in DARK_INK_FILLS) else WHITE))
    ax.set_xticks(range(len(flags)))
    ax.set_xticklabels([label for _, label, _ in flags], color=INK, fontsize=8.5)
    ax.set_yticks(range(len(agents)))
    ax.set_yticklabels([f"{a} · {data['roles'].get(a) or 'agent'}" for a in reversed(agents)], color=INK)
    ax.set_xlim(-0.6, len(flags) - 0.4)
    ax.set_ylim(-0.6, len(agents) - 0.4)
    ax.spines["bottom"].set_visible(False)
    fig.savefig(out, bbox_inches="tight", facecolor=SURFACE)
    with (out.with_suffix(".csv")).open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["agent", "role", *(flag for flag, _, _ in flags)])
        for agent in agents:
            verdict = data["verdicts"].get(agent) or {}
            writer.writerow([agent, data["roles"].get(agent) or "", *(verdict.get(flag) for flag, _, _ in flags)])
    return out


def plot_run(run_dir: Path, out_dir: Path, *, log=print) -> list[Path]:
    """Every figure the run's data allows, as PNG with a CSV twin, under out_dir. Each path written is logged as
    `== plot <path>` and returned."""
    data = load_run(Path(run_dir))
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = [plot_timeline(data, out_dir / "timeline.png"), plot_board(data, out_dir / "board.png"),
               plot_verdicts(data, out_dir / "verdicts.png")]
    paths = [path for path in written if path is not None]
    for path in paths:
        log(f"== plot {path}")
    return paths
