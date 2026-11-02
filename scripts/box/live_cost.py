"""What the live runs on boxes have cost so far, priced from their own token counts, before OpenRouter bills them.

    uv run python -m scripts.box.live_cost 58710271 58710274            # each live run, dearest first, with a total
    uv run python -m scripts.box.live_cost 58710271 --since snap.json   # also the $/h since an earlier --save
    uv run python -m scripts.box.live_cost 58710271 --save snap.json

Each box's Inspect sample buffers hold every model call of its live runs. A small script goes to the box over
box_ssh.sh's standard input and sums the calls' usage per run and model (input, cache reads and writes, output); it
reads and never writes. This side prices them at src/models/model_registry.yaml's dated prices. On 2026-11-01 the
same pricing matched the Opus pilot's billed cost within 3%, while OpenRouter's own count lagged by about $80.

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

REMOTE = r'''
import glob, json, os, re, sqlite3
from collections import defaultdict
for db in sorted(glob.glob("/root/.local/share/inspect_ai/samplebuffer/*/*.db")):
    m = re.search(r"_([a-z0-9-]+)_[A-Za-z0-9]{22}\.eval", os.path.basename(db))
    run = m.group(1) if m else os.path.basename(db)
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        rows = con.execute("select data from events where data like '%\"event\": \"model\"%'").fetchall()
    except sqlite3.Error as e:
        print(json.dumps({"run": run, "error": str(e)}))
        continue
    acc = defaultdict(lambda: defaultdict(float))
    for (data,) in rows:
        try:
            ev = json.loads(data)
        except ValueError:
            continue
        if ev.get("event") != "model" or ev.get("pending"):
            continue
        u = ((ev.get("output") or {}).get("usage")) or {}
        a = acc[ev.get("model")]
        a["calls"] += 1
        a["input"] += u.get("input_tokens") or 0
        a["cache_read"] += u.get("input_tokens_cache_read") or 0
        a["cache_write"] += u.get("input_tokens_cache_write") or 0
        a["output"] += u.get("output_tokens") or 0
    for model, a in acc.items():
        print(json.dumps({"run": run, "model": model, **a}))
'''


def price(model: str, input_tokens: float, cache_read: float, cache_write: float, output: float) -> float | None:
    """Dollars for this usage at the registry's prices; None for a model the registry does not price. Inspect's input
    count excludes cache reads and writes, so they are added back for the registry's formula."""
    name = model[len("swarm/"):] if model.startswith("swarm/") else model
    prices = models.usd_per_million(name)
    if not prices:
        return None
    # usage summed over many calls: the per-call long-prompt tier (GPT-6 Luna over 272k tokens) does not apply
    flat = {k: v for k, v in prices.items() if k != "long_prompt"}
    return models.cost_usd(flat, int(input_tokens + cache_read + cache_write), int(cache_read), int(output),
                           cache_write=int(cache_write))


def box_usage(box_id: str) -> list[dict]:
    """Every live run's usage per model on one box, read through box_ssh.sh."""
    out = subprocess.run([str(BOX_FOLDER / "box_ssh.sh"), box_id, "python3 -"], input=REMOTE, capture_output=True,
                         text=True, timeout=120)
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
        dollars = price(r["model"], r["input"], r["cache_read"], r["cache_write"], r["output"]) or 0.0
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
