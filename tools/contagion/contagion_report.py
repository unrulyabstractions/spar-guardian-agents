"""The contagion tool's outputs: the fits of one board, curves.csv, fit.json, contagion.png and the summary payload."""
from __future__ import annotations

import csv
import json
from pathlib import Path

from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from matplotlib.ticker import MaxNLocator

from tools.contagion.contagion_models import LABELS, Settings, fit
from tools.contagion.contagion_observed import BEHAVIOURS, Board, adoption_curve

# The palette of src/ui/run_figures.py (not importable here: it needs ORBIT), and its categorical slots for the models.
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_SECONDARY = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
MODEL_COLOURS = {"si": "#2a78d6", "threshold": "#eb6834", "sir": "#1baf7a"}
TITLES = {"dishonest": "dishonest: cheating onset", "honest": "honest: intervention or disclosure"}
DPI = 160


def observed_curves(board: Board) -> dict[str, list[float]]:
    """Each behaviour's curve over all agents and over the agents that are not regulators."""
    others = [agent for agent in board.agents if board.roles.get(agent) != "regulator"]
    curves = {}
    for behaviour in BEHAVIOURS:
        curves[behaviour] = adoption_curve(board.onsets[behaviour], board.agents, board.rounds)
        curves[f"{behaviour}_without_regulators"] = adoption_curve(board.onsets[behaviour], others, board.rounds)
    return curves


def degenerate_reasons(board: Board, behaviour: str, min_agents: int) -> list[str]:
    """Why the behaviour's fit would mean nothing; empty when it can be fitted."""
    reasons = []
    n = len(board.agents)
    if n < min_agents:
        reasons.append(f"{n} agents: a share moves in steps of 1/{n}, too coarse to tell the models apart "
                       f"(the fit needs at least {min_agents})")
    if not board.onsets_known:
        reasons.append(f"the onsets come from the {board.onset_source}, which gives no round, so every adopter "
                       f"is placed at the last round")
    times = [time for time in board.onsets[behaviour].values() if time is not None]
    if not times:
        reasons.append(f"no agent turned {behaviour}")
    elif len(set(times)) == 1:
        reasons.append(f"every {behaviour} agent appeared at round {times[0]}, so nothing spreads beyond the seeds")
    return reasons


def fit_board(board: Board, settings: Settings) -> dict[str, dict]:
    """Each behaviour's fits, {model: fit} with seeds and start round, or {"skipped": reasons}."""
    curves = observed_curves(board)
    fits = {}
    for behaviour in BEHAVIOURS:
        reasons = degenerate_reasons(board, behaviour, settings.min_agents)
        if reasons:
            fits[behaviour] = {"skipped": reasons}
            continue
        onsets = board.onsets[behaviour]
        start = min(time for time in onsets.values() if time is not None)
        seeds = sorted(agent for agent, time in onsets.items() if time == start)
        observed = curves[behaviour][start:]
        fits[behaviour] = {model: {"seeds": seeds, "start_round": start,
                                   **fit(model, board.graph, seeds, observed, settings)}
                           for model in settings.models}
    return fits


def write_curves(path: Path, board: Board, fits: dict[str, dict], models: tuple[str, ...]) -> None:
    """curves.csv: one row per round, the observed shares and each fitted model's mean and percentiles."""
    curves = observed_curves(board)
    observed_columns = [f"observed_{key}" for key in curves]
    model_columns = [f"{model}_{behaviour}_{stat}" for behaviour in BEHAVIOURS for model in models
                     for stat in ("mean", "p5", "p95")]
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["round", *observed_columns, *model_columns])
        for r in range(board.rounds + 1):
            row = [r, *(round(curves[key][r], 6) for key in curves)]
            for behaviour in BEHAVIOURS:
                for model in models:
                    result = fits[behaviour].get(model)
                    for stat in ("mean", "p5", "p95"):
                        row.append(model_value(result, stat, r))
            writer.writerow(row)


def model_value(result: dict | None, stat: str, r: int) -> str | float:
    """The fitted curve's value at round r; empty before the seeds' round or without a fit."""
    if not result or r < result["start_round"]:
        return ""
    return round(result[stat][r - result["start_round"]], 6)


def write_fit(path: Path, fits: dict[str, dict]) -> None:
    """fit.json: every fit without its curves (they are in curves.csv)."""
    trimmed = {behaviour: {model: ({key: value for key, value in result.items() if key not in ("mean", "p5", "p95")}
                                   if isinstance(result, dict) else result)
                           for model, result in by_model.items()}
               for behaviour, by_model in fits.items()}
    path.write_text(json.dumps(trimmed, indent=2) + "\n")


def plot(path: Path, board: Board, fits: dict[str, dict], models: tuple[str, ...]) -> None:
    """contagion.png: per behaviour, the observed share against each fitted model's mean and 5-95% band."""
    curves = observed_curves(board)
    rounds = list(range(board.rounds + 1))
    fig = Figure(figsize=(11, 4.6), dpi=DPI, facecolor=SURFACE)
    axes = fig.subplots(1, 2, sharey=True)
    for ax, behaviour in zip(axes, BEHAVIOURS):
        style_axes(ax, TITLES[behaviour])
        ax.set_xlabel("round", color=INK_SECONDARY, fontsize=9)
        ax.step(rounds, curves[behaviour], where="post", color=INK, linewidth=1.8)
        ax.step(rounds, curves[f"{behaviour}_without_regulators"], where="post",
                color=MUTED, linewidth=1.3, linestyle=(0, (3, 2)))
        result = fits[behaviour]
        if "skipped" in result:
            note = "no fit: " + "; ".join(result["skipped"])
            ax.text(0.02, 0.97, wrap(note, 60), transform=ax.transAxes, va="top", ha="left", color=INK_SECONDARY,
                    fontsize=8, bbox=dict(boxstyle="round,pad=0.4", facecolor=SURFACE, edgecolor=GRID))
            continue
        for model in models:
            fitted = result[model]
            xs = list(range(fitted["start_round"], board.rounds + 1))
            colour = MODEL_COLOURS[model]
            ax.fill_between(xs, fitted["p5"], fitted["p95"], color=colour, alpha=0.13, linewidth=0)
            ax.plot(xs, fitted["mean"], color=colour, linewidth=1.4)
    axes[0].set_ylabel("share of agents adopted", color=INK_SECONDARY, fontsize=9)
    axes[0].set_ylim(-0.02, 1.04)
    label = "SYNTHETIC board" if board.synthetic else "run"
    fig.suptitle(f"{label} {board.name}: observed spread against fitted contagion models", x=0.06, ha="left",
                 color=INK, fontsize=11, fontweight="bold")
    fig.text(0.06, 0.905, subtitle(board), color=INK_SECONDARY, fontsize=8.5, ha="left")
    handles = [Line2D([], [], color=INK, linewidth=1.8, label="observed, all agents"),
               Line2D([], [], color=MUTED, linewidth=1.3, linestyle=(0, (3, 2)),
                      label="observed, without regulators")]
    if any("skipped" not in result for result in fits.values()):
        for model in models:
            handles.append(Line2D([], [], color=MODEL_COLOURS[model], linewidth=1.4, label=f"{LABELS[model]} mean"))
        handles.append(Patch(facecolor=MUTED, alpha=0.25, label="5th to 95th percentile"))
    fig.legend(handles=handles, loc="lower left", bbox_to_anchor=(0.06, 0.0), ncol=3, frameon=False, fontsize=8,
               labelcolor=INK_SECONDARY)
    fig.subplots_adjust(top=0.82, bottom=0.25, wspace=0.08)
    fig.savefig(path, facecolor=SURFACE, dpi=DPI)


def style_axes(ax, title: str) -> None:
    """The chrome of src/ui/run_figures.py: surface, no top or right spine, muted ticks, a grid on y."""
    ax.set_facecolor(SURFACE)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(AXIS)
    ax.tick_params(colors=MUTED, labelcolor=INK_SECONDARY, length=0)
    ax.yaxis.grid(True, color=GRID, linewidth=1)
    ax.xaxis.set_major_locator(MaxNLocator(integer=True))
    ax.set_axisbelow(True)
    ax.set_title(title, loc="left", color=INK, fontsize=10, pad=8)


def subtitle(board: Board) -> str:
    """The figure's second line: size, graph, and where the onsets came from."""
    regulators = sum(1 for role in board.roles.values() if role == "regulator")
    return (f"{len(board.agents)} agents ({regulators} regulators), {board.rounds} rounds, "
            f"graph: {board.graph_source}, onsets: {board.onset_source}")


def wrap(text: str, width: int) -> str:
    """The text broken into lines of at most width characters at spaces."""
    lines, line = [], ""
    for word in text.split():
        if line and len(line) + 1 + len(word) > width:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    return "\n".join([*lines, line])


def summary_payload(board: Board, fits: dict[str, dict], settings: Settings, files: list[str]) -> dict:
    """The summary.json payload (the module docstring of tools.contagion has its schema)."""
    curves = observed_curves(board)
    skipped = {behaviour: result["skipped"] for behaviour, result in fits.items() if "skipped" in result}
    reason = " ".join(f"{behaviour}: {'; '.join(reasons)}." for behaviour, reasons in skipped.items())
    compact = {behaviour: ({"skipped": result["skipped"]} if "skipped" in result else
                           {model: {"parameters": fitted["parameters"], "final_size": fitted["final_size"],
                                    "ssr": fitted["ssr"]} for model, fitted in result.items()})
               for behaviour, result in fits.items()}
    payload = {
        "synthetic": board.synthetic,
        "graph_source": board.graph_source,
        "graph_edges": board.graph.number_of_edges(),
        "onset_source": board.onset_source,
        "onsets_known": board.onsets_known,
        "n_agents": len(board.agents),
        "n_regulators": sum(1 for role in board.roles.values() if role == "regulator"),
        "rounds": board.rounds,
        "models": list(settings.models),
        "simulations": settings.simulations,
        "seed": settings.seed,
        "observed_final": {key: curve[-1] for key, curve in curves.items()},
        "fits": compact,
        "degenerate": bool(skipped),
        "reason": reason,
        "notes": board.notes,
        "files": files,
    }
    if board.synthetic:
        payload["planted"] = board.planted
    return payload


def write_outputs(board: Board, settings: Settings, directory: Path, prefix: str) -> dict:
    """Fit the board, write curves.csv, fit.json and contagion.png (each with the prefix), return the payload."""
    directory.mkdir(parents=True, exist_ok=True)
    fits = fit_board(board, settings)
    files = [f"{prefix}curves.csv", f"{prefix}fit.json", f"{prefix}contagion.png"]
    write_curves(directory / files[0], board, fits, settings.models)
    write_fit(directory / files[1], fits)
    plot(directory / files[2], board, fits, settings.models)
    return summary_payload(board, fits, settings, files)
