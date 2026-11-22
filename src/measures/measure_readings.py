"""What independent readings of ian_baseline_explorations_1's transcripts found of each code rung and judge label, from
the one hand-kept file measure_readings.yaml beside this module. The transcript viewer's How reliable section
(src/ui/transcript_ladder_tables.py) and the paper's reliability table (scripts/paper/paper_sources.py) both read it
through readings(); nothing else keeps these counts.

    r = readings()
    r["about"]          # the paragraph above the table
    r["measures"]       # {measure: its name}, in the table's order
    r["passes"]         # {pass: {"label", "read_on", "what", "current", "retired"}}; exactly one pass is current
    r["rows"]           # [{"measure", "value", "pass", "chosen", "read", "held", "wrong", "unclear", "note",
                        #   "retired"}, ...]
    r["conclusions"]    # {"disclosure", "control", "mislead", "excerpt"}: what a reader may conclude of the scores

The code rungs were read before their fixes. The scored labels, disclosure and manipulation (its kinds mislead and
control apart), and the check that locates each excerpt were read on the current pass. Two earlier passes read the yes
or no labels (scheming, awareness, manipulation) that the scored labels replaced on 2026-11-19: such a pass is
retired, and its rows carry "retired" True and follow every current row. readings() refuses a measure it does not
know, a ladder's row that names no rung of that ladder, a row of an unknown pass, a retired row before a current one,
and counts that do not add up (held + wrong + unclear = read wherever read is counted).
"""
from __future__ import annotations

from pathlib import Path

import yaml

from src.measures import collusion
from src.measures.rungs import RUNGS

READINGS_YAML = Path(__file__).with_name("measure_readings.yaml")
MEASURES = {"cheating": RUNGS, "collusion": collusion.RUNGS, "disclosure": None, "manipulation": None,
            "excerpt": None, "scheming": None, "awareness": None}
"""The measures the file may hold, with the rungs a ladder's rows must name (None for a judge's verdicts)."""
KEYS = {"about", "measures", "passes", "rows", "conclusions"}
PASS_FIELDS = {"label", "read_on", "what", "current", "retired"}
CONCLUSIONS = {"disclosure", "control", "mislead", "excerpt"}
"""What a reader may conclude: of the disclosure score, of manipulation's two kinds, and of the located excerpts."""
ROW_FIELDS = ("measure", "value", "pass", "chosen", "read", "held", "wrong", "unclear", "note")


def _check_counts(row: dict) -> None:
    read, held, wrong, unclear = row["read"], row["held"], row["wrong"], row["unclear"]
    where = f"{row['measure']} {row['value']} ({row['pass']})"
    if any(n is not None and (not isinstance(n, int) or n < 0) for n in (read, held, wrong, unclear)):
        raise ValueError(f"measure_readings.yaml: {where} has a count that is not a whole number")
    if read is None:
        if held is not None:
            raise ValueError(f"measure_readings.yaml: {where} holds {held} of no counted reading")
        return
    if None in (held, wrong, unclear) or held + wrong + unclear != read:
        raise ValueError(f"measure_readings.yaml: {where} holds {held}, wrong {wrong}, unclear {unclear} of {read}")


def readings(path: Path = READINGS_YAML) -> dict:
    """The file, checked: every measure known and named, every pass with its fields and exactly one current, which is
    not retired, every row with all its fields, a ladder's value one of its rungs, the rows of retired passes after
    every other row, counts that add up, and every conclusion. Each row gets "retired": whether its pass is retired."""
    data = yaml.safe_load(path.read_text())
    if set(data) != KEYS:
        raise ValueError(f"measure_readings.yaml holds {sorted(data)}, not {sorted(KEYS)}")
    if set(data["measures"]) - set(MEASURES):
        raise ValueError(f"measure_readings.yaml names {sorted(set(data['measures']) - set(MEASURES))}, "
                         "which no measure is")
    if set(data["conclusions"]) != CONCLUSIONS:
        raise ValueError(f"measure_readings.yaml concludes on {sorted(data['conclusions'])}, not {sorted(CONCLUSIONS)}")
    passes = data["passes"]
    for key, about in passes.items():
        if set(about) != PASS_FIELDS:
            raise ValueError(f"measure_readings.yaml: pass {key} has {sorted(about)}, not {sorted(PASS_FIELDS)}")
        about["read_on"] = str(about["read_on"])
    if sum(bool(p["current"]) for p in passes.values()) != 1:
        raise ValueError("measure_readings.yaml: exactly one pass must be current")
    if any(p["current"] and p["retired"] for p in passes.values()):
        raise ValueError("measure_readings.yaml: the current pass cannot be retired")
    retired_seen = False
    for row in data["rows"]:
        if set(row) != set(ROW_FIELDS):
            raise ValueError(f"measure_readings.yaml: a row has {sorted(row)}, not {sorted(ROW_FIELDS)}")
        if row["measure"] not in data["measures"]:
            raise ValueError(f"measure_readings.yaml: {row['measure']!r} is no measure")
        rungs = MEASURES[row["measure"]]
        if rungs is not None and row["value"] not in rungs:
            raise ValueError(f"measure_readings.yaml: {row['value']!r} is no rung of {row['measure']}")
        if row["pass"] not in passes:
            raise ValueError(f"measure_readings.yaml: {row['pass']!r} is no pass")
        _check_counts(row)
        row["retired"] = bool(passes[row["pass"]]["retired"])
        if retired_seen and not row["retired"]:
            raise ValueError(f"measure_readings.yaml: {row['measure']} {row['value']} ({row['pass']}) follows a row "
                             "of a retired pass")
        retired_seen = retired_seen or row["retired"]
    return data
