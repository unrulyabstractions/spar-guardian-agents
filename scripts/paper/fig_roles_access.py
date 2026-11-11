"""Figure 2 of the paper: the roles and what each holds in the two studies, as a 200-dpi PNG at
tmp/figures/paper/fig2_roles_access.png.

    uv run python -m scripts.paper.fig_roles_access

Left, the multi-domain study, read from runs/study_exploration_1/INDEX.jsonl and each valid run's run.json: the
teams that held both a regulator and a disregulator, what each role held (access.agents) and which roles get submit
(SwarmConfig.holds_submit on the run's own config). Right, the SWE deep dive, read from Lucy's Stage 2 batch: the
access map of every episode in submodules/lucy/runs/s2_swe/plan.json (seat k as the plan's seat_label names it), seat
3's role by arm from analysis/summary.json, and submit from SwarmConfig.holds_submit. Every count is checked to agree
across the runs it comes from. No network, no model calls.
"""
from __future__ import annotations

import json
from collections import defaultdict

from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from scripts.paper.figures import FULL, agent, arrow, blank, box, edge_point, role_legend, toward
from src.configuration import access_policy
from src.configuration.settings import ROOT
from src.configuration.swarm_config import SwarmConfig
from src.utils.figure_style import INK, INK_SECONDARY, MUTED, PLANE, PRINT_DPI, SURFACE, WHITE

OUT = ROOT / "tmp" / "figures" / "paper" / "fig2_roles_access.png"
STUDY_INDEX = ROOT / "runs" / "study_exploration_1" / "INDEX.jsonl"
STAGE2 = ROOT / "submodules" / "lucy" / "runs" / "s2_swe"
ROLES = ("worker", "regulator", "disregulator")

LEFT_NOTE = ("R oversees and hands in nothing. D is privately told\n"
             "to get the result by any means. Neither holds a piece.")
RIGHT_NOTE = ("Stage 1: seat 3 works and can push, but cannot submit.\n"
              "Stage 2: seat 3 only oversees. Control: a third worker.")


def one(values: set, what: str):
    """The single value every run agrees on, or an error naming the disagreement."""
    if len(values) != 1:
        raise SystemExit(f"fig_roles_access: the runs disagree on {what}: {sorted(values, key=repr)}")
    return next(iter(values))


def multi_domain() -> dict:
    """The study's teams with a regulator and a disregulator: how many of each role, how many items each worker
    holds, what the roles hold, and which roles get submit. Each must agree across every such valid run."""
    rows = [json.loads(line) for line in STUDY_INDEX.read_text().splitlines() if line.strip()]
    teams, held, submit, runs = set(), defaultdict(set), set(), 0
    for r in rows:
        if not r["valid"] or r["mock"]:
            continue
        run = json.loads((STUDY_INDEX.parent / r["path"] / "run.json").read_text())
        swarm = run.get("swarm")
        if not swarm:   # a lone agent: no team, no roles
            continue
        holds = run["access"]["agents"]
        if swarm["regulators"] or swarm["disregulators"]:
            for name, role in swarm["roles"].items():
                if role != "worker":
                    held[role].add(len(holds[name]["holds"]))
        if not (swarm["regulators"] and swarm["disregulators"]):
            continue
        runs += 1
        workers = sorted(len(holds[n]["holds"]) for n, role in swarm["roles"].items() if role == "worker")
        teams.add((swarm["workers"], swarm["regulators"], swarm["disregulators"], tuple(workers)))
        config = SwarmConfig.model_validate(run["config"])
        submit.add(tuple(config.holds_submit(role) for role in ROLES))
    n_workers, n_reg, n_dis, worker_items = one(teams, "the team")
    return {"workers": n_workers, "regulators": n_reg, "disregulators": n_dis, "worker_items": worker_items,
            "role_items": {role: one(v, f"what a {role} holds") for role, v in held.items()},
            "submit": dict(zip(ROLES, one(submit, "who gets submit"))), "runs": runs}


def deep_dive() -> dict:
    """Stage 2 of the deep dive: what W1, W2 and seat 3 hold in every episode, seat 3's role in each arm, and which
    roles get submit."""
    plan = json.loads((STAGE2 / "plan.json").read_text())
    held, regulators, team = set(), defaultdict(set), set()
    for ep in plan["episodes"]:
        seats = ep["seats"]
        team.add(len(seats["seat_label"]))
        fields = dict(f.split("=", 1) for f in ep["fields"])
        amap = access_policy.parse(fields["access"])
        by_id = {agent_id: amap[label] for agent_id, label in seats["seat_label"].items()}
        held.add(tuple((seat, tuple(by_id[seats[seat]])) for seat in ("W1", "W2", "seat3")))
        regulators[ep["arm"]].add(int(fields.get("regulators", 0)))
    rows = json.loads((STAGE2 / "analysis" / "summary.json").read_text())["rows"]
    seat3 = defaultdict(set)
    for r in rows:
        if r["seat"] == "seat3":
            seat3[r["arm"]].add(r["role"] or "worker")
    seat3_role = {arm: one(v, f"seat 3's role in {arm}") for arm, v in seat3.items()}
    overseen = [arm for arm, role in seat3_role.items() if role == "regulator"]
    config = SwarmConfig(n_agents=one(team, "the team size"),
                         regulators=one(regulators[overseen[0]], "the regulators"))
    return {"held": dict(one(held, "the access map")), "seat3_role": seat3_role,
            "submit": {role: config.holds_submit(role) for role in ROLES}, "episodes": len(plan["episodes"])}


def diamond(ax, x, y, s=14):
    ax.scatter([x], [y], marker="D", s=s, c=INK, zorder=9, edgecolors=SURFACE, linewidths=0.6)


def pieces(items) -> str:
    if not items:
        return "no piece"
    return ("piece " if len(items) == 1 else "pieces ") + " + ".join(str(i) for i in items)


def panel(fig, rect, title, seats, outside, submit, note):
    """One study: the board, the seats in boxes with what they hold, the agents outside the boxes, the submitted
    work and the note. seats is [(label, role, text, muted)], outside [(label, role, side)]."""
    ax = fig.add_axes(rect)
    blank(ax, (-3.3, 3.3), (-3.45, 3.05))
    ax.text(0, 2.95, title, ha="center", va="top", fontsize=8.5, weight="bold")
    bw, bh, by = 1.6, 0.55, 2.15
    box(ax, -bw / 2, by - bh / 2, bw, bh, "board", fc=PLANE, ec=INK_SECONDARY, fs=7.5)
    y, w = 0.1, 1.45
    xs = [(k - (len(seats) - 1) / 2) * 1.6 for k in range(len(seats))]
    for x, (label, role, text, muted) in zip(xs, seats):
        box(ax, x - w / 2, y - 0.65, w, 1.45, None, fc=WHITE, ec=INK_SECONDARY, z=1)
        ax.text(x, y + 0.53, text, ha="center", va="center", fontsize=6.8, color=MUTED if muted else INK)
        agent(ax, x, y - 0.12, label, role, r=0.32, fs=6.5)
        arrow(ax, (x, y + 0.8), edge_point(0, by, bw, bh, x, y + 0.8), lw=0.7, color=MUTED, head=4)
        if submit[role]:
            diamond(ax, x + 0.3, y + 0.18)
            arrow(ax, (x, y - 0.65), (x * 0.35, -1.72), lw=0.7, color=MUTED, head=4)
    edge = max(xs) + w / 2 + 0.5
    for label, role, side in outside:
        x, ry = side * edge, y + 0.75
        agent(ax, x, ry, label, role, r=0.32, fs=6.5)
        arrow(ax, toward(x, ry, 0, by, 0.38), edge_point(0, by, bw, bh, x, ry), lw=0.7, color=MUTED, head=4)
        if submit[role]:
            diamond(ax, x + 0.3, ry + 0.3)
    box(ax, -1.3, -2.38, 2.6, 0.62, "submitted work", fc=PLANE, ec=INK_SECONDARY, fs=7)
    ax.text(0, -2.68, note, ha="center", va="top", fontsize=6.3, style="italic", color=INK_SECONDARY,
            linespacing=1.3)
    return ax


def draw(md: dict, dd: dict) -> Figure:
    fig = Figure(figsize=(FULL, 2.95))
    FigureCanvasAgg(fig)
    workers = [(f"W{k}", "worker", f"piece {k}" if n == 1 else f"{n} pieces", False)
               for k, n in enumerate(md["worker_items"], 1)]
    outside = [*[("R", "regulator", -1)] * md["regulators"], *[("D", "disregulator", 1)] * md["disregulators"]]
    panel(fig, [0.0, 0.13, 0.5, 0.87], "Multi-domain study", workers, outside, md["submit"], LEFT_NOTE)
    seat3 = dd["seat3_role"]
    s_role = "regulator" if "regulator" in seat3.values() else "worker"
    seats = [("W1", "worker", pieces(dd["held"]["W1"]), False), ("W2", "worker", pieces(dd["held"]["W2"]), False),
             ("S", s_role, pieces(dd["held"]["seat3"]), not dd["held"]["seat3"])]
    panel(fig, [0.5, 0.13, 0.5, 0.87], "SWE deep dive", seats, [], dd["submit"], RIGHT_NOTE)
    role_legend(fig, 0.06, extra=[(lambda ax, x, y: diamond(ax, x, y, s=16), "can submit the team's work")],
                compact=True)
    return fig


def main() -> int:
    """Read both sources, print what the figure draws, write the PNG and print its path."""
    md, dd = multi_domain(), deep_dive()
    print(f"multi-domain: {md['runs']} valid runs with a regulator and a disregulator; {md['workers']} workers "
          f"holding {list(md['worker_items'])} item(s), {md['regulators']} regulator(s), {md['disregulators']} "
          f"disregulator(s); roles hold {md['role_items']} item(s); submit {md['submit']}")
    print(f"deep dive: {dd['episodes']} Stage 2 episodes; held {dd['held']}; seat 3 by arm {dd['seat3_role']}; "
          f"submit {dd['submit']}")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    draw(md, dd).savefig(OUT, dpi=PRINT_DPI)
    print(OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
