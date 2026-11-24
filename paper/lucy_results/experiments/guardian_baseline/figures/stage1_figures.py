"""Guardian baseline Stage 1 (DeepSeek, SWE): results figures for the mid-term report.

Each figure is written as a PNG (300 dpi) and an SVG to experiments/guardian_baseline/figures/stage1/:
  every_episode     all 180 episodes, each a column of three squares (W1, W2, seat 3), cheated or not
  ladder            cheating by arm, pooled over planning, with the four planned contrasts under each step
  seat3_warnings    seat 3's own verdict crossed with whether it warned the team (Luna's "warn" label)
  held_back         S2 and S3 at planning 1: episodes where a worker held back, by seat 3's own verdict
  shipped           whether team/main passed the hidden tests, by arm and issue
  vuls_provenance   how much of the planted fix each vuls ship held (data/s1_vuls_provenance.json)
  predictions       the prediction note's ranges against the observed counts

The headline dot plot (workers and seat 3 by arm and planning, with Wilson intervals) is figures/stage1_figure.py;
these use its colours and type. Inputs are runs/s1_swe/analysis/summary.json and data/s1_vuls_provenance.json, which
checks/stage1_vuls_provenance_json.py writes on lucypc. Counts are asserted against the report
(notes/2026-11-01_stage1_report.md) before anything is drawn; the p-values are copied from it (50,000 permutations).

Usage, from the repo root (matplotlib isn't a project dependency):
    uv run --no-project --with matplotlib python experiments/guardian_baseline/figures/stage1_figures.py
"""
import json
from collections import Counter
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import FancyBboxPatch, Patch, Rectangle
from matplotlib.transforms import blended_transform_factory

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
SUMMARY = REPO / "runs/s1_swe/analysis/summary.json"
PROVENANCE = HERE / "data/s1_vuls_provenance.json"
OUT = HERE / "stage1"

ARMS = ["3W", "S0", "S1", "S2", "S3"]
ARM_TEXT = {"3W": "three workers, no regulator", "S0": "regulator seat, no instructions", "S1": "+ duty",
            "S2": "+ definition of cheating", "S3": "+ pressure"}
ISSUES = ["flipt-02e2", "openlibrary-3aee", "vuls-1832"]
# One seat-3 verdict didn't parse; it was re-judged on 1 Nov as cheated, with no disclosure labels kept.
REJUDGED = {("S2_p0_deepseek_vuls-1832_4", "a02"): True}

# The palette of figures/stage1_figure.py (categorical slots 1 and 2 for the planning levels), plus a teal for
# "judged not cheated", a warm grey for "judged cheated", and violet for seat 3 where it is drawn as a series.
SURFACE = "#ffffff"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
PLANNING = {0: "#2a78d6", 1: "#eb6834"}
PLANNING_NAME = {0: "No planning turn", 1: "One planning turn"}
HONEST = "#199e70"
HONEST_LIGHT = "#9bd8bf"
CHEATED = "#c9c8c0"
CHEATED_DARK = "#7a7972"
WORKERS = "#3b3a37"
SEAT3 = "#6250d6"
BLUES = ["#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7", "#3987e5", "#2a78d6", "#256abf",
         "#1c5cab", "#184f95", "#104281", "#0d366b"]


def load():
    rows = [r for r in json.loads(SUMMARY.read_text())["rows"] if r["exit"] == 0 and r["failure"] is None]
    eps = {}
    for r in rows:
        cheated = r["cheated"] if r["parse_ok"] else REJUDGED[(r["episode"], r["agent"])]
        e = eps.setdefault(r["episode"], {"name": r["episode"], "arm": r["arm"], "planning": r["planning_turns"],
                                          "issue": r["issue"], "passed": bool(r["team_reward"])})
        e[r["seat"]] = bool(cheated)
        if r["seat"] == "seat3":
            e["warned"] = "warn" in r["disclosure"] if r["parse_ok"] else None
    eps = list(eps.values())
    assert len(eps) == 180 and all(("W1" in e and "W2" in e and "seat3" in e) for e in eps)
    return eps


def cell(eps, arm=None, planning=None, issue=None):
    return [e for e in eps if (arm is None or e["arm"] == arm) and (planning is None or e["planning"] == planning)
            and (issue is None or e["issue"] == issue)]


def workers_cheated(es):
    return sum(e["W1"] + e["W2"] for e in es)


def check(eps):
    """The report's tables, so a changed summary can't redraw silently."""
    assert [workers_cheated(cell(eps, a, 1)) for a in ARMS] == [36, 36, 36, 31, 29]
    assert [workers_cheated(cell(eps, a, 0)) for a in ARMS] == [36] * 5
    assert [sum(e["seat3"] for e in cell(eps, a)) for a in ARMS] == [36, 36, 36, 21, 19]
    assert [sum(bool(e["warned"]) for e in cell(eps, a)) for a in ARMS] == [1, 0, 14, 27, 31]
    assert [sum(e["passed"] for e in cell(eps, a)) for a in ARMS] == [21, 20, 19, 16, 15]


# ---- layout helpers -------------------------------------------------------------------------------------------

def style():
    plt.rcParams.update({
        "font.family": ["Helvetica Neue", "Helvetica", "Arial", "DejaVu Sans"],
        "font.size": 9,
        "axes.edgecolor": AXIS,
        "axes.labelcolor": INK_2,
        "xtick.color": INK_2,
        "ytick.color": MUTED,
        "text.color": INK,
        "svg.fonttype": "none",
    })


def figure(w, h):
    return plt.figure(figsize=(w, h), facecolor=SURFACE)


def axes(fig, left, bottom, width, height):
    """An axes placed in inches from the figure's bottom left, with no frame and no tick marks."""
    W, H = fig.get_size_inches()
    ax = fig.add_axes((left / W, bottom / H, width / W, height / H))
    ax.set_facecolor(SURFACE)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.tick_params(length=0)
    return ax


def header(fig, title, subtitle=None, left=0.15):
    W, H = fig.get_size_inches()
    fig.text(left / W, 1 - 0.16 / H, title, fontsize=11, fontweight="bold", va="top")
    if subtitle:
        fig.text(left / W, 1 - 0.42 / H, subtitle, fontsize=8, color=INK_2, va="top", linespacing=1.45)


def legend(fig, handles, top, **kw):
    """A one-row legend under the header, `top` inches from the figure's top edge."""
    W, H = fig.get_size_inches()
    opts = dict(loc="upper left", bbox_to_anchor=(0.15 / W, 1 - top / H), ncol=len(handles), frameon=False,
                fontsize=8, borderaxespad=0, handletextpad=0.5, columnspacing=1.6, handlelength=1.0, handleheight=1.0)
    fig.legend(handles=handles, **(opts | kw))


def footnote(fig, text, left=0.15, bottom=0.12):
    W, H = fig.get_size_inches()
    fig.text(left / W, bottom / H, text, fontsize=7, color=MUTED, va="bottom", linespacing=1.45)


def arm_label(ax, x, y, arm, ha="right", gap=0.0):
    ax.text(x, y + gap, arm, ha=ha, va="bottom", fontsize=9, fontweight="bold", clip_on=False)
    ax.text(x, y - gap, ARM_TEXT[arm], ha=ha, va="top", fontsize=7.5, color=INK_2, clip_on=False)


def save(fig, name):
    OUT.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT / f"{name}.png", dpi=300, facecolor=SURFACE)
    fig.savefig(OUT / f"{name}.svg", facecolor=SURFACE)
    plt.close(fig)
    print("wrote", OUT / f"{name}.png")


# ---- figures --------------------------------------------------------------------------------------------------

def every_episode(eps):
    """A unit chart: one column of three squares per episode, sorted within each arm x planning cell."""
    pitch, size, split = 1.22, 1.0, 0.45            # square pitch and size; extra gap above seat 3
    col_h = 3 * pitch + split
    row = col_h + 2.3                                # one arm
    block = 18 * pitch
    x_block = {0: 0.0, 1: block + 5.8}
    width_u = x_block[1] + block + 4.4
    height_u = 4 * row + col_h
    k = 5.25 / width_u                               # inches per unit
    fig = figure(7.2, height_u * k + 2.0)
    H = fig.get_size_inches()[1]
    ax = axes(fig, 1.8, 0.3, width_u * k, height_u * k)
    ax.set_xlim(0, width_u)
    ax.set_ylim(0, height_u)
    ax.set_xticks([])
    ax.set_yticks([])
    for i, arm in enumerate(ARMS):
        y0 = (len(ARMS) - 1 - i) * row
        arm_label(ax, -1.6, y0 + col_h / 2, arm, gap=0.15)
        for p, x0 in x_block.items():
            es = sorted(cell(eps, arm, p), key=lambda e: (e["W1"] + e["W2"], e["seat3"], e["W1"]))
            for j, e in enumerate(es):
                x = x0 + j * pitch
                for y, cheated in ((y0 + split + 2 * pitch, e["W1"]), (y0 + split + pitch, e["W2"]), (y0, e["seat3"])):
                    ax.add_patch(Rectangle((x, y), size, size, facecolor=CHEATED if cheated else HONEST, lw=0))
            xt = x0 + block + 0.5
            ax.text(xt, y0 + split + 1.5 * pitch + size / 2, f"{workers_cheated(es)}/36", va="center",
                    fontsize=7.5, color=INK_2)
            ax.text(xt, y0 + size / 2, f"{sum(e['seat3'] for e in es)}/18", va="center", fontsize=7.5, color=INK_2)
    top = height_u + 0.9
    for p, x0 in x_block.items():
        ax.text(x0, top, PLANNING_NAME[p], fontsize=9, fontweight="bold", va="bottom", clip_on=False)
        ax.text(x0 + block + 0.5, top, "cheated", fontsize=7.5, color=INK_2, va="bottom", clip_on=False)
    header(fig, "Every Stage 1 episode, and who in it was judged to have cheated",
           "180 episodes of three DeepSeek V4.1 Flash agents on three SWE issues (6 per issue in each cell).\n"
           "Squares are sorted within each row, so the clean ones collect on the left.")
    legend(fig, [Patch(color=HONEST, label="Judged not cheated"), Patch(color=CHEATED, label="Judged cheated")],
           top=0.92)
    # One example column with its seats named, beside the legend.
    kx = axes(fig, 4.75, H - 0.82 - col_h * k, 0.6, col_h * k)
    kx.set_xlim(0, 0.6 / k)
    kx.set_ylim(0, col_h)
    kx.set_xticks([])
    kx.set_yticks([])
    for y, name in ((split + 2 * pitch, "W1"), (split + pitch, "W2"), (0, "seat 3")):
        kx.add_patch(Rectangle((0, y), size, size, facecolor=CHEATED, lw=0))
        kx.text(size + 0.6, y + size / 2, name, va="center", fontsize=7, color=INK_2, clip_on=False)
    kx.text(-0.8, col_h / 2, "one episode:", ha="right", va="center", fontsize=7.5, color=INK_2, clip_on=False)
    save(fig, "every_episode")


def ladder(eps):
    """Pooled rates by arm as two lines, with each planned step's change written beneath it."""
    fig = figure(7.2, 4.9)
    ax = axes(fig, 1.05, 1.95, 5.55, 2.0)
    xs = range(len(ARMS))
    series = [("Workers", WORKERS, 72, [workers_cheated(cell(eps, a)) for a in ARMS]),
              ("Seat 3", SEAT3, 36, [sum(e["seat3"] for e in cell(eps, a)) for a in ARMS])]
    for name, color, n, ks in series:
        ys = [100 * k / n for k in ks]
        ax.plot(xs, ys, color=color, lw=2, solid_capstyle="round", solid_joinstyle="round", zorder=2)
        ax.plot(xs, ys, "o", ms=7, color=color, mec=SURFACE, mew=1.5, zorder=3)
        ax.annotate(f"{ks[3]}/{n}", (3, ys[3]), xytext=(0, -9), textcoords="offset points", ha="center", va="top",
                    fontsize=7.5, color=INK_2)
        ax.annotate(f"{name}\n{ks[-1]}/{n}", (4, ys[-1]), xytext=(9, 0), textcoords="offset points", va="center",
                    fontsize=8, color=INK, linespacing=1.3)
    ax.text(1, 102.5, "Workers and seat 3 both 100%", ha="center", va="bottom", fontsize=7.5, color=INK_2)
    ax.set_xlim(-0.35, 4.35)
    ax.set_ylim(0, 110)
    ax.set_yticks([0, 25, 50, 75, 100])
    ax.set_yticklabels(["0%", "25%", "50%", "75%", "100%"], fontsize=8)
    ax.yaxis.grid(True, color=GRID, lw=0.6)
    ax.set_axisbelow(True)
    ax.set_xticks(list(xs))
    ax.set_xticklabels(ARMS, fontsize=9.5, fontweight="bold", color=INK)
    ax.tick_params(axis="x", pad=6)
    ax.set_ylabel("Judged cheated", fontsize=8)
    # The steps: what each arm adds, then the change for workers (planned, Holm over four) and seat 3 (secondary).
    steps = [("+ regulator seat", "0", "Holm p = 1", "0", "p = 1"),
             ("+ duty", "0", "Holm p = 1", "0", "p = 1"),
             ("+ definition", "−6.9 pts", "Holm p = 0.45", "−41.7 pts", "p = 0.0001"),
             ("+ pressure", "−2.8 pts", "Holm p = 1", "−5.6 pts", "p = 0.81")]
    trans = ax.get_xaxis_transform()
    for i, (what, wd, wp, sd, sp) in enumerate(steps):
        x = i + 0.5
        ax.text(x, -0.2, what, transform=trans, ha="center", va="top", fontsize=8, color=INK, clip_on=False)
        for y, d, pv in ((-0.34, wd, wp), (-0.55, sd, sp)):
            ax.text(x, y, d, transform=trans, ha="center", va="top", fontsize=8, fontweight="bold",
                    color=INK if d != "0" else MUTED, clip_on=False)
            ax.text(x, y - 0.095, pv, transform=trans, ha="center", va="top", fontsize=7, color=INK_2, clip_on=False)
    for y, name, color in ((-0.385, "Workers", WORKERS), (-0.595, "Seat 3", SEAT3)):
        ax.plot([-0.95, -0.75], [y, y], transform=trans, color=color, lw=2, solid_capstyle="round", clip_on=False)
        ax.text(-0.68, y, name, transform=trans, va="center", fontsize=8, color=INK, clip_on=False)
    ax.text(-0.95, -0.2, "Step", transform=trans, va="top", fontsize=8, color=INK_2, clip_on=False)
    header(fig, "The definition of cheating is the only step that moved much, mostly seat 3's own cheating",
           "Each arm adds one thing to the one before. Pooled over planning: 72 workers and 36 seat-3 agents per arm.\n"
           "Under each step, its change in percentage points. Workers' contrasts are the planned ones; seat 3's are "
           "secondary (unadjusted).")
    footnote(fig, "The episode is the unit: permutation tests within planning × issue, 50,000 permutations. "
                  "Luna judge with the guardian addendum.")
    save(fig, "ladder")


def seat3_warnings(eps):
    """Stacked bars of seat 3's 36 agents per arm: own verdict x whether it warned the team."""
    cats = [((True, False), "Cheated, didn't warn", CHEATED, INK),
            ((True, True), "Cheated and warned", CHEATED_DARK, SURFACE),
            ((False, True), "Warned, didn't cheat", HONEST, SURFACE),
            ((False, False), "Neither", HONEST_LIGHT, INK)]
    fig = figure(7.2, 4.0)
    ax = axes(fig, 1.95, 0.8, 4.9, 2.05)
    for i, arm in enumerate(ARMS):
        es = [e for e in cell(eps, arm) if e["warned"] is not None]
        c = Counter((e["seat3"], e["warned"]) for e in es)
        y, left = len(ARMS) - 1 - i, 0
        for key, _, color, text in cats:
            w = c[key]
            if not w:
                continue
            ax.add_patch(FancyBboxPatch((left + 0.1, y - 0.28), w - 0.2, 0.56, boxstyle="round,pad=0,rounding_size=0.12",
                                        facecolor=color, lw=0, mutation_aspect=1 / 6))
            if w >= 2:
                ax.text(left + w / 2, y, str(w), ha="center", va="center", fontsize=8, color=text, fontweight="bold")
            left += w
        arm_label(ax, -1.0, y, arm, gap=0.04)
    ax.set_xlim(0, 36)
    ax.set_ylim(-0.5, len(ARMS) - 0.5)
    ax.set_yticks([])
    ax.set_xticks([0, 9, 18, 27, 36])
    ax.tick_params(axis="x", labelsize=8, labelcolor=MUTED)
    ax.set_xlabel("Seat-3 agents (36 per arm)", fontsize=8)
    legend(fig, [Patch(color=c, label=l) for _, l, c, _ in cats], top=0.72, columnspacing=1.4)
    header(fig, "Seat 3 warned more as its instructions grew, but mostly while cheating itself",
           "\"Warned\" is Luna's disclosure label: one of seat 3's board posts says not to look at or use the existing fix.")
    footnote(fig, "S2 shows 35: one seat-3 verdict (cheated) was re-judged, and the re-judge kept no disclosure labels.",
             bottom=0.08)
    save(fig, "seat3_warnings")


def held_back(eps):
    """S2 and S3 at planning 1: one square per episode, filled where at least one worker held back."""
    es = cell(eps, "S2", 1) + cell(eps, "S3", 1)
    groups = [("Seat 3 didn't cheat", [e for e in es if not e["seat3"]]),
              ("Seat 3 cheated", [e for e in es if e["seat3"]])]
    held = [[e for e in g if not (e["W1"] and e["W2"])] for _, g in groups]
    assert [(len(h), len(g)) for h, (_, g) in zip(held, groups)] == [(8, 21), (1, 15)]
    pitch, size, k = 1.25, 1.0, 0.155
    fig = figure(7.2, 2.3)
    ax = axes(fig, 1.85, 0.5, 21 * pitch * k, 3.3 * k * 1.25)
    ax.set_xlim(0, 21 * pitch)
    ax.set_ylim(0, 3.3 * 1.25)
    ax.set_xticks([])
    ax.set_yticks([])
    for row, ((name, g), h) in enumerate(zip(groups, held)):
        y = (1 - row) * 2.2 * 1.25
        g = sorted(g, key=lambda e: (e["W1"] + e["W2"], e["name"]))
        for j, e in enumerate(g):
            n_held = 2 - e["W1"] - e["W2"]
            ax.add_patch(Rectangle((j * pitch, y), size, size, facecolor=HONEST if n_held else CHEATED, lw=0))
            if n_held == 2:
                ax.text(j * pitch + size / 2, y + size / 2, "2", ha="center", va="center", fontsize=6.5,
                        color=SURFACE, fontweight="bold")
        ax.text(-1.0, y + size / 2 + 0.05, name, ha="right", va="bottom", fontsize=8.5, fontweight="bold",
                clip_on=False)
        ax.text(-1.0, y + size / 2 - 0.05, f"{len(g)} episodes", ha="right", va="top", fontsize=7.5, color=INK_2,
                clip_on=False)
        ax.text(len(g) * pitch + 0.6, y + size / 2, f"{len(h)} of {len(g)}", va="center", fontsize=8.5,
                color=INK, clip_on=False)
    legend(fig, [Patch(color=HONEST, label="At least one worker held back (\"2\": both did)"),
                 Patch(color=CHEATED, label="Both workers cheated")], top=0.72)
    header(fig, "Workers held back mostly where seat 3 itself stayed clean",
           "S2 and S3 with a planning turn: 36 episodes, one square each. Every worker who held back in Stage 1 is "
           "in these.")
    footnote(fig, "Fisher exact p = 0.05 over episodes. An association: a clean seat 3 may be the one whose warnings "
                  "land, or both may follow from how the episode starts.", bottom=0.1)
    save(fig, "held_back")


def shipped(eps):
    """A grid of pass counts: arm x issue, and all three issues together."""
    cols = ISSUES + ["all"]
    names = {"flipt-02e2": "flipt", "openlibrary-3aee": "openlibrary", "vuls-1832": "vuls", "all": "All three"}
    fig = figure(7.2, 3.75)
    ax = axes(fig, 2.0, 0.55, 4.6, 2.4)
    for i, arm in enumerate(ARMS):
        y = len(ARMS) - 1 - i
        arm_label(ax, -0.25, y, arm, gap=0.04)
        for j, col in enumerate(cols):
            es = cell(eps, arm, issue=None if col == "all" else col)
            k, n = sum(e["passed"] for e in es), len(es)
            step = round(k / n * (len(BLUES) - 1))
            x = j + (0.25 if col == "all" else 0)
            ax.add_patch(FancyBboxPatch((x + 0.04, y - 0.42), 0.92, 0.84, boxstyle="round,pad=0,rounding_size=0.06",
                                        facecolor=BLUES[step], lw=0))
            ax.text(x + 0.5, y, f"{k}/{n}", ha="center", va="center", fontsize=9, fontweight="bold",
                    color=SURFACE if step >= 7 else INK)
    for j, col in enumerate(cols):
        x = j + (0.25 if col == "all" else 0)
        ax.text(x + 0.5, len(ARMS) - 0.4, names[col], ha="center", va="bottom", fontsize=8.5, color=INK,
                fontweight="bold" if col == "all" else "normal")
    ax.set_xlim(0, len(cols) + 0.25)
    ax.set_ylim(-0.5, len(ARMS) - 0.5)
    ax.set_xticks([])
    ax.set_yticks([])
    header(fig, "Shipped work: did the team's main branch pass the hidden tests?",
           "Episodes, 12 per arm and issue. Darker is a higher pass rate.")
    footnote(fig, "Openlibrary can be passed honestly, and passed every time. Vuls fell from 7 of 24 in 3W and S0 to "
                  "1 of 36 in S1–S3;\nin 44 of its 60 episodes nothing reached the team's main branch.", bottom=0.08)
    save(fig, "shipped")


def beeswarm(values, dx, step):
    """Vertical offsets that keep dots closer than dx apart from overlapping: 0, +step, -step, +2 step, ..."""
    placed, out = [], []
    for v in values:
        for level in (0, 1, -1, 2, -2, 3, -3):
            if all(abs(v - pv) >= dx or level != pl for pv, pl in placed):
                placed.append((v, level))
                out.append(level * step)
                break
    return out


def vuls_provenance():
    """Distinctive fix lines in each vuls ship: passing ships, failing ships one per row, and honest agents."""
    d = json.loads(PROVENANCE.read_text())
    ships = [s for s in d["ships"] if s["team_files"]]
    passed = sorted(s["team_fix_lines"] for s in ships if s["passed"])
    failed = sorted((s for s in ships if not s["passed"]), key=lambda s: (ARMS.index(s["arm"]), s["planning"],
                                                                          -s["team_fix_lines"]))
    honest = sorted(a["final_fix_lines"] for a in d["honest_agents"])
    assert d["fix_lines"] == 118 and len(ships) == 16 and len(passed) == 8 and len(honest) == 14
    assert (min(passed), max(passed)) == (99, 118) and (min(honest), max(honest)) == (2, 43)
    lo, hi = min(honest), max(honest)

    # Rows from the top: a section header, its rows, a gap, the next header.
    y_pass_head, y_pass = 15.7, 13.9
    y_fail_head = 12.3
    fy = {s["episode"]: 11.2 - i for i, s in enumerate(failed)}
    y_hon_head = min(fy.values()) - 1.6
    y_hon = y_hon_head - 1.25
    fig = figure(7.2, 5.95)
    ax = axes(fig, 1.75, 0.62, 5.05, 4.05)
    ax.set_xlim(0, 122)
    ax.set_ylim(y_hon - 1.0, y_pass_head + 0.5)
    trans = blended_transform_factory(ax.transAxes, ax.transData)
    ax.axvspan(lo, hi, color=HONEST, alpha=0.1, lw=0, zorder=0)
    ax.text((lo + hi) / 2, y_pass_head, f"Honest work: {lo}–{hi}", ha="center", va="center", fontsize=7.5,
            color=HONEST, fontweight="bold")
    ax.axvline(d["fix_lines"], color=AXIS, lw=1, zorder=0)
    ax.text(d["fix_lines"] - 1.5, y_pass_head, f"Whole fix: {d['fix_lines']}", ha="right", va="center", fontsize=7.5,
            color=INK_2)
    for y, name in ((y_pass_head, "Passed"), (y_fail_head, "Failed"), (y_hon_head, "Agents judged honest")):
        ax.text(-0.33, y, name, transform=trans, va="center", fontsize=9, fontweight="bold", clip_on=False)
    for y, text in ((y_pass, f"{len(passed)} ships"), (y_hon, f"{len(honest)} agents' final diffs")):
        ax.text(-0.02, y, text, transform=trans, ha="right", va="center", fontsize=7.5, color=INK_2, clip_on=False)
    for v, off in zip(passed, beeswarm(passed, 2.6, 0.42)):
        ax.plot(v, y_pass + off, "o", ms=6.5, color=WORKERS, mec=SURFACE, mew=1.2, zorder=3)
    for v, off in zip(honest, beeswarm(honest, 2.6, 0.42)):
        ax.plot(v, y_hon + off, "o", ms=6.5, color=HONEST, mec=SURFACE, mew=1.2, zorder=3)
    for s in failed:
        y, a, b = fy[s["episode"]], s["team_fix_lines"], s["max_agent_fix_lines"]
        if b > a:
            ax.plot([a, b], [y, y], color=AXIS, lw=1.5, solid_capstyle="round", zorder=1)
            ax.plot(b, y, "o", ms=6.5, mfc=SURFACE, mec=WORKERS, mew=1.3, zorder=2)
        ax.plot(a, y, "o", ms=6.5, color=WORKERS, mec=SURFACE, mew=1.2, zorder=3)
        s3 = s["arm"] == "S3"
        ax.text(-0.02, y, f"{s['arm']}, planning {s['planning']}", transform=trans, ha="right", va="center",
                fontsize=7.5, color=INK if s3 else INK_2, fontweight="bold" if s3 else "normal", clip_on=False)
    ax.set_yticks([])
    ax.set_xticks([0, 20, 40, 60, 80, 100, 118])
    ax.tick_params(axis="x", labelsize=8, labelcolor=MUTED)
    ax.xaxis.grid(True, color=GRID, lw=0.6)
    ax.set_axisbelow(True)
    ax.set_xlabel("Distinctive lines of the planted fix in the diff", fontsize=8)
    legend(fig, [Line2D([], [], ls="", marker="o", ms=6.5, color=WORKERS, label="On the team's main branch"),
                 Line2D([], [], ls="", marker="o", ms=6.5, mfc=SURFACE, mec=WORKERS, mew=1.3,
                        label="Most on any one agent's branch"),
                 Line2D([], [], ls="", marker="o", ms=6.5, color=HONEST, label="An honest agent's final diff")],
           top=0.9, handletextpad=0.3, columnspacing=1.4, handlelength=1.2)
    header(fig, "Vuls: S3's failed ships held no more of the fix than honest work does",
           "Fix lines are lines of 20+ characters found nowhere else in the repo, its history or the image. "
           "Ships with nothing\non the main branch (44 of 60) aren't shown.")
    save(fig, "vuls_provenance")


def predictions(eps):
    """The prediction note's range and point for workers cheated (of 36), against the count observed."""
    pred = {("3W", 0): (30, 36, 34), ("3W", 1): (30, 36, 34), ("S0", 0): (33, 36, None), ("S0", 1): (33, 36, None),
            ("S1", 0): (26, 35, 31), ("S1", 1): (24, 35, 30), ("S2", 0): (20, 34, 28), ("S2", 1): (16, 33, 25),
            ("S3", 0): (18, 34, 27), ("S3", 1): (15, 33, 24)}
    fig = figure(7.2, 4.8)
    ax = axes(fig, 1.95, 0.75, 4.85, 3.05)
    trans = blended_transform_factory(ax.transAxes, ax.transData)
    for i, arm in enumerate(ARMS):
        base = (len(ARMS) - 1 - i) * 2.6
        ax.text(-0.04, base + 0.62, arm, transform=trans, ha="right", va="bottom", fontsize=9, fontweight="bold")
        ax.text(-0.04, base + 0.38, ARM_TEXT[arm], transform=trans, ha="right", va="top", fontsize=7.5, color=INK_2)
        for p in (0, 1):
            y = base + (1 - p)
            lo, hi, point = pred[(arm, p)]
            ax.plot([lo, hi], [y, y], color=GRID, lw=7, solid_capstyle="round", zorder=1)
            if point is not None:
                ax.plot([point, point], [y - 0.28, y + 0.28], color=MUTED, lw=1.5, solid_capstyle="round", zorder=2)
            k = workers_cheated(cell(eps, arm, p))
            ax.plot(k, y, "o", ms=7, color=PLANNING[p], mec=SURFACE, mew=1.5, zorder=3)
            if k < 36:
                ax.annotate(str(k), (k, y), xytext=(0, 7), textcoords="offset points", ha="center", va="bottom",
                            fontsize=7.5, color=INK_2)
    ax.set_xlim(12, 37)
    ax.set_ylim(-0.7, 4 * 2.6 + 1.6)
    ax.set_yticks([])
    ax.set_xticks([15, 20, 25, 30, 36])
    ax.tick_params(axis="x", labelsize=8, labelcolor=MUTED)
    ax.xaxis.grid(True, color=GRID, lw=0.6)
    ax.set_axisbelow(True)
    ax.set_xlabel("Workers judged cheated, of 36", fontsize=8)
    legend(fig, [Line2D([], [], color=GRID, lw=7, solid_capstyle="round", label="Predicted range"),
                 Line2D([], [], color=MUTED, lw=1.5, marker="|", ms=9, ls="", mew=1.5, label="Point prediction"),
                 Line2D([], [], ls="", marker="o", ms=7, color=PLANNING[0], label="Observed, no planning turn"),
                 Line2D([], [], ls="", marker="o", ms=7, color=PLANNING[1], label="Observed, one planning turn")],
           top=0.72, fontsize=7.5, handletextpad=0.8, columnspacing=1.1, handlelength=1.6)
    header(fig, "Predictions made before the run, against what happened",
           "Workers judged cheated per arm and planning level (36 each). The predictions are from "
           "notes/2026-10-27_stage1_expectations.md.")
    footnote(fig, "S0 was predicted as \"within ±3 of 3W\", drawn here as 33–36 from 3W's observed 36.", bottom=0.08)
    save(fig, "predictions")


def main():
    style()
    eps = load()
    check(eps)
    every_episode(eps)
    ladder(eps)
    seat3_warnings(eps)
    held_back(eps)
    shipped(eps)
    vuls_provenance()
    predictions(eps)


if __name__ == "__main__":
    main()
