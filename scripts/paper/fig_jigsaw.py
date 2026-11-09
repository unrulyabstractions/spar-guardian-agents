"""Figure 3 of the paper, the jigsaw-puzzle exploit, as a 200-dpi PNG at tmp/figures/paper/fig3_jigsaw.png
(gitignored).

    uv run python -m scripts.paper.fig_jigsaw

One diagram for software engineering, protein design and creative writing: each task's honeypot, the split that deals
one puzzle piece to each worker in its sandbox, the board that joins the workers, and the honeypot rebuilt from the
pieces posted there. Everything the diagram claims is read from the code at build time: the team is the default
preset's (src/configuration/presets.py), the pieces and who holds them come from each domain's plan under that config
(src/domains/*, src/configuration/access_policy.py), and the caption over the workers comes from the seats each
domain's tools bind (one service per agent is a machine each, one service with a user per agent is a user each). The
titles, tasks, icons and honeypot names are the setup figures' (scripts/paper/domain_figures.py SPECS). The build
fails when a domain does not deal exactly one piece to each worker, when the domains deal their pieces in different
seat orders, or when a text spills its box. No network, no model calls.
"""
from __future__ import annotations

import sys
from collections import Counter

import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

from scripts.paper.domain_figures import (
    PIECE_FILL,
    SPECS,
    XS,
    Canvas,
    H,
    agent,
    arrow,
    bubble,
    frame,
    icon,
    puzzle,
    tabs,
)
from src.configuration.presets import PRESETS
from src.configuration.settings import ROOT
from src.configuration.task_names import names_of
from src.domains import DOMAINS
from src.domains.creative import creative_sandbox
from src.domains.swe import swe_sandbox
from src.sandbox.machines import agent_machines
from src.utils.figure_style import HONEY, INK, INK_SECONDARY, MUTED, PLANE, PRINT_DPI, SURFACE

OUT = ROOT / "tmp" / "figures" / "paper" / "fig3_jigsaw.png"
KEYS = ("swe", "protein_design", "creative_writing")   # the figure's tasks, in its order
# Each domain's seats as its tools() binds them for a team: SWE and protein design a service per agent, creative
# writing a Linux user per agent in the one container.
SEATS = {"swe": lambda plan: swe_sandbox.seats(plan.agents),
         "protein_design": lambda plan: agent_machines.machine_seats(plan.agents),
         "creative_writing": creative_sandbox.seats}
LAYOUT_TEXT = {"machine": "one machine per agent", "user": "one user per agent"}


def layout(key: str, plan) -> str:
    """machine when each agent has a service of its own, user when one service holds a Linux user per agent."""
    seats = SEATS[key](plan).values()
    n = len(plan.agents)
    services, users = {s.service for s in seats}, {s.user for s in seats}
    if len(services) == n:
        return "machine"
    if len(services) == 1 and len(users) == n:
        return "user"
    raise ValueError(f"{key}: seats fit neither one machine nor one user per agent: {list(seats)}")


def dealt(key: str, config) -> tuple[list[int], int, str]:
    """For one domain under the config: the index, in the domain's deal order, of the piece each worker holds in seat
    order, the number of pieces, and the layout. ValueError unless each worker holds one piece and together they hold
    every piece."""
    domain = DOMAINS[key]()
    plan = domain.plan(config)
    workers = [a for a in config.seats if (plan.roles or {}).get(a, "worker") == "worker"]
    items = domain.split_items(len(workers))
    held = [plan.holds[a] for a in workers]
    if not plan.split or any(len(h) != 1 for h in held) or sorted(h[0] for h in held) != sorted(items):
        raise ValueError(f"{key}: the split does not give each worker one piece of {items}: {plan.holds}")
    return [items.index(h[0]) for h in held], len(items), layout(key, plan)


def caption(layouts: dict[str, str]) -> str:
    """The layout most tasks use, then each other task's in brackets."""
    common = Counter(layouts.values()).most_common(1)[0][0]
    others = [f"{names_of(k).display}: {LAYOUT_TEXT[v]}" for k, v in layouts.items() if v != common]
    return LAYOUT_TEXT[common] + (f" ({'; '.join(others)})" if others else "")


def draw() -> None:
    config = PRESETS["default"]
    specs = {k: next(s for s in SPECS if s["name"] == f"setup_{k}") for k in KEYS}
    deals = {k: dealt(k, config) for k in KEYS}
    orders = {tuple(d[0]) for d in deals.values()}
    if len(orders) != 1 or len({d[1] for d in deals.values()}) != 1:
        raise ValueError(f"the tasks deal their pieces differently, one picture cannot show them: {deals}")
    holds, n = list(orders.pop()), deals[KEYS[0]][1]
    if len(holds) > len(XS) or n > len(PIECE_FILL):
        raise ValueError(f"{len(holds)} workers and {n} pieces do not fit the drawing's {len(XS)} columns")
    xs = XS[:len(holds)]

    c = Canvas()
    ax = c.ax
    c.text(0.4, H - 0.55, " / ".join(specs[k]["title"] for k in KEYS), (0.3, H - 0.9, 19.8, H - 0.2), fontsize=12,
           weight="bold", va="center")
    tasks = " / ".join(specs[k]["task"].split(";")[0] for k in KEYS)
    c.text(0.4, H - 1.15, tasks, (0.3, H - 1.45, 19.8, H - 0.9), fontsize=8, color=INK_SECONDARY, va="center")

    # the honeypots, one icon per task
    icon_x = [0.85, 2.3, 3.75]
    for k, x in zip(KEYS, icon_x):
        icon(ax, specs[k]["icon"], x, 4.4, 1.4)
    for a, b in zip(icon_x, icon_x[1:]):
        c.text((a + b) / 2, 4.4, "/", ((a + b) / 2 - 0.25, 3.9, (a + b) / 2 + 0.25, 4.9), fontsize=11, color=HONEY,
               ha="center", va="center")
    names = " /\n".join(specs[k]["honeypot"] for k in KEYS)
    c.text(2.3, 2.95, names, (0.2, 2.05, 4.4, 3.75), fontsize=8.5, color=HONEY, ha="center", va="center",
           weight="bold", linespacing=1.25)

    # the sandbox: a frame per worker, captioned with each task's layout
    top = 7.05
    for x in xs:
        frame(ax, x - 1.3, 2.6, x + 1.3, top)
    text = caption({k: d[2] for k, d in deals.items()})
    c.text((xs[0] + xs[-1]) / 2, top + 0.22, text, (xs[0] - 1.3, top, xs[-1] + 1.3, top + 0.5), fontsize=7.5,
           color=MUTED, ha="center", va="bottom")

    # each worker with the piece the split dealt it
    s = 1.3
    for k, (i, x) in enumerate(zip(holds, xs)):
        agent(ax, x, 5.75, f"worker {k + 1}")
        left, right = tabs(i, n)
        puzzle(ax, x - s / 2, 3.0, s, left, right, PIECE_FILL[i])
    arrow(ax, (4.45, 4.4), (xs[0] - 1.45, 4.4), HONEY, lw=1.6)
    c.text(5.2, 4.62, "split", (4.5, 4.45, 5.95, 4.95), fontsize=8, color=HONEY, ha="center", va="bottom")

    # the board joins the workers
    ax.add_patch(FancyBboxPatch((xs[0] - 1.3, 0.55), xs[-1] - xs[0] + 2.6, 1.0,
                                boxstyle="round,pad=0,rounding_size=0.2", fc=PLANE, ec=INK_SECONDARY, lw=1.1, zorder=3))
    bubble(ax, xs[0] - 0.55, 1.08)
    c.text(xs[0] - 0.05, 1.05, "board", (xs[0] - 0.1, 0.6, xs[0] + 1.6, 1.5), fontsize=9, color=INK, va="center",
           weight="bold")
    for x in xs:
        ax.plot([x, x], [2.6, 1.55], color=MUTED, lw=1.0, ls=(0, (2, 2)), zorder=2)

    # the pieces meet on the board and rebuild the honeypot
    cx = 17.5
    sx = cx - n * s / 2
    for i in range(n):
        left, right = tabs(i, n)
        puzzle(ax, sx + i * s, 4.3, s, left, right, PIECE_FILL[i], z=4 + i)
    c.text(cx, 3.75, "the honeypot, rebuilt", (15.2, 3.45, 19.8, 4.05), fontsize=8.5, color=HONEY, ha="center",
           va="center", weight="bold")
    arrow(ax, (xs[-1] + 1.3, 1.05), (cx, 3.4), INK_SECONDARY, lw=1.2, ls=(0, (3, 2)),
          style="angle,angleA=0,angleB=90,rad=8")
    c.text((xs[-1] + 1.3 + cx) / 2, 1.25, "posted", (xs[-1] + 1.4, 1.1, cx - 0.1, 1.6), fontsize=7.5,
           color=INK_SECONDARY, ha="center", va="bottom")

    c.check("fig3_jigsaw")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    c.fig.savefig(OUT, dpi=PRINT_DPI, facecolor=SURFACE)
    plt.close(c.fig)
    print(f"team {config.n_agents} ({config.mode}, access {config.access}); pieces {n}; workers hold {holds}; "
          f"layouts {({k: d[2] for k, d in deals.items()})}")
    print(OUT)


def main() -> int:
    """Draw the figure and print its path; 1 when the code contradicts the diagram or a text spills its box."""
    try:
        draw()
    except ValueError as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
