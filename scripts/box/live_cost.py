"""What the live runs on boxes have cost so far, priced from their own token counts, before OpenRouter bills them.

    uv run python -m scripts.box.live_cost 58710271 58710274            # each live run, dearest first, with a total
    uv run python -m scripts.box.live_cost 58710271 --since snap.json   # also the $/h since an earlier --save
    uv run python -m scripts.box.live_cost 58710271 --save snap.json

Each box's Inspect sample buffers hold every model call of its live runs. A small script goes to the box over
box_ssh.sh's standard input and sums the calls' usage per run and model (input, cache reads and writes, output), with
the calls over the model's long-prompt line apart; it reads and never writes. This side prices them at
src/models/model_registry.yaml's dated prices.

A buffer holds each finished call in two rows of one uuid, so the script counts a uuid once. Until 2026-11-02 it counted
both, and every figure from it was exactly double: the eight stopped regulator runs of 2026-11-01 cost $88.28, not
$176.56, which the key's own usage confirms within $5. OpenRouter's count never lagged by the $80 this file once said.

Run it in the first 10 minutes of every new cell, and again later with --since to see each run's burn. A run whose
cost so far is already a large share of its dollar cap (cost_limit_usd) is the one to stop.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from src.models import model_registry as models

BOX_FOLDER = Path(__file__).resolve().parent
BUFFERS = "/root/.local/share/inspect_ai/samplebuffer"
"""Where Inspect keeps a box's live runs."""

REMOTE = r'''
import glob, json, os, re, sqlite3
from collections import defaultdict
LINES = json.loads(__LINES__)
for db in sorted(glob.glob(__BUFFERS__ + "/*/*.db")):
    m = re.search(r"_([a-z0-9-]+)_[A-Za-z0-9]{22}\.eval", os.path.basename(db))
    run = m.group(1) if m else os.path.basename(db)
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        rows = con.execute("select data from events where data like '%\"event\": \"model\"%'").fetchall()
    except sqlite3.Error as e:
        print(json.dumps({"run": run, "error": str(e)}))
        continue
    acc = defaultdict(lambda: defaultdict(float))
    seen = set()
    for (data,) in rows:
        try:
            ev = json.loads(data)
        except ValueError:
            continue
        if ev.get("event") != "model" or ev.get("pending") or ev.get("uuid") in seen:
            continue
        seen.add(ev.get("uuid"))
        u = ((ev.get("output") or {}).get("usage")) or {}
        tokens = {"input": u.get("input_tokens") or 0, "cache_read": u.get("input_tokens_cache_read") or 0,
                  "cache_write": u.get("input_tokens_cache_write") or 0, "output": u.get("output_tokens") or 0}
        model = ev.get("model")
        line = LINES.get(model[len("swarm/"):] if str(model).startswith("swarm/") else model)
        over = line is not None and tokens["input"] + tokens["cache_read"] + tokens["cache_write"] > line
        a = acc[(model, over)]
        a["calls"] += 1
        for key, count in tokens.items():
            a[key] += count
    for (model, over), a in acc.items():
        print(json.dumps({"run": run, "model": model, "long": over, **a}))
'''


def remote_script(buffers: str = BUFFERS) -> str:
    """The script a box runs, with each priced model's long-prompt line, which the box has no registry to read."""
    lines = {entry["inspect"]: entry["long_prompt"]["over"] for entry in models.entries().values()
             if entry.get("long_prompt")}
    return REMOTE.replace("__LINES__", repr(json.dumps(lines))).replace("__BUFFERS__", repr(buffers))


def price(model: str, input_tokens: float, cache_read: float, cache_write: float, output: float,
          long: bool = False) -> float | None:
    """Dollars for this usage at the registry's prices, or at its long-prompt rates for usage summed over calls past
    the model's line; None for a model the registry does not price. Inspect's input count excludes cache reads and
    writes, so they are added back for the registry's formula."""
    name = model[len("swarm/"):] if model.startswith("swarm/") else model
    prices = models.usd_per_million(name)
    if not prices:
        return None
    # the sum of many calls crosses the line whatever the calls did, so the row, not the sum, says which rates apply
    rates = prices["long_prompt"] if long else prices
    flat = {key: rate for key, rate in rates.items() if key not in ("long_prompt", "over")}
    return models.cost_usd(flat, int(input_tokens + cache_read + cache_write), int(cache_read), int(output),
                           cache_write=int(cache_write))


def box_usage(box_id: str) -> list[dict]:
    """Every live run's usage per model on one box, read through box_ssh.sh."""
    out = subprocess.run([str(BOX_FOLDER / "box_ssh.sh"), box_id, "python3 -"], input=remote_script(),
                         capture_output=True, text=True, timeout=120)
    rows = []
    for line in out.stdout.splitlines():
        try:
            rows.append({"box": box_id, **json.loads(line)})
        except json.JSONDecodeError:
            continue
    return rows


def costs(rows: list[dict]) -> dict[tuple[str, str], float]:
    """{(box, run): dollars so far}; an unpriced model counts as nothing and is reported by the caller."""
    out: dict[tuple[str, str], float] = {}
    for r in rows:
        if "error" in r:
            continue
        dollars = price(r["model"], r["input"], r["cache_read"], r["cache_write"], r["output"],
                        r.get("long", False)) or 0.0
        out[(r["box"], r["run"])] = out.get((r["box"], r["run"]), 0.0) + dollars
    return out


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("boxes", nargs="+", help="vast.ai instance ids")
    parser.add_argument("--save", type=Path, help="write this reading for a later --since")
    parser.add_argument("--since", type=Path, help="an earlier --save: also print each run's $/h since then")
    args = parser.parse_args(argv)
    rows = [row for box in args.boxes for row in box_usage(box)]
    unpriced = sorted({r["model"] for r in rows if "error" not in r and price(r["model"], 0, 0, 0, 0) is None})
    now = {"time": time.time(), "costs": {f"{b}|{r}": c for (b, r), c in costs(rows).items()}}
    before = json.loads(args.since.read_text()) if args.since else None
    minutes = (now["time"] - before["time"]) / 60 if before else None
    for key, dollars in sorted(now["costs"].items(), key=lambda kv: -kv[1]):
        box, run = key.split("|", 1)
        line = f"{box} {run[:60]:60s} ${dollars:8.2f}"
        if before and minutes:
            delta = dollars - before["costs"].get(key, 0.0)
            line += f"   +${delta:6.2f} in {minutes:.1f} min = ${60 * delta / minutes:7.2f}/h"
        print(line)
    print(f"== {len(now['costs'])} live runs on {len(args.boxes)} boxes: ${sum(now['costs'].values()):.2f} so far")
    if unpriced:
        print(f"!! no registry price, counted as $0: {', '.join(unpriced)}")
    if args.save:
        args.save.write_text(json.dumps(now))
    return 1 if unpriced else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
