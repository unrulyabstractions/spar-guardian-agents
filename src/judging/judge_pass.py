"""`python -m src judge-pass`: a judge pass over runs' stored records, under a dollar cap, into judges/ alone.

    python -m src judge-pass cheatbench runs/study_exploration_1 --judge gpt-6-luna --cap 5 --limit 10
    python -m src judge-pass collusion <run> [<run> ...] --judge gpt-6-luna --cap 2 [--batch] [--only-missing]

Passes (PASSES):
- cheatbench: CheatBench's judge on every stored trajectory CheatBench would judge (cheatbench_units), one call each.
- collusion: the Collusion ladder's judge on every collaborating pair (src/judging/collusion_judge.py), one call each.

A pass reads each run's stored record (run.json, episodes.jsonl, the transcripts, the boards) and writes only its
verdict file, runs/<study>/<run>/judges/<pass>.<judge>.jsonl (src/judging/verdict_files.py), after every call. It
never reads an eval log for a verdict and never re-exports a run. A study folder stands for its valid runs, as the
transcript viewer lists them.

Money. The pass refuses to start without a cap in dollars, and for a judge whose model has no registry price or no
max_output. Before each call it reserves the most that call can cost: every byte of the message and of the schema
as an input token (a byte-level tokenizer gives at most one token per byte) plus the model's max_output as output,
at the registry's rates (the batch rates with --batch; a prompt past the long-prompt line at that line's rates). A call
whose reservation would take the spent and reserved dollars past the cap is not made, and no later call is: the pass
stops there. After each call its reservation is replaced by its cost from its own token counts. So the cap is never
passed. It prints a line per call, then the calls, their tokens and their dollars.

Units already judged. By default a unit whose file holds a parsed row for this judge is skipped, and a unit whose
row failed is judged again (CheatBench's judge.py does the same). --only-missing judges only units with no row at
all, so failures are not retried; --redo judges every unit again. A failed call is recorded as parse_ok False with
its error, and the pass goes on. --limit N judges the first N units to judge, the pilot. The normal API is the default;
--batch uses the provider's batch API (half price, up to a day).
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from src.domains import BY_TASK
from src.export.run_export import VERDICT_IDENTITY
from src.judging import cheatbench_judge as judging
from src.judging import verdict_files
from src.judging.collusion_judge import collusion_units
from src.measures.run_directory import RunDir
from src.measures.run_listing import list_collection
from src.models.model_registry import cost_usd
from src.utils.cheatbench_core import judge as cb_judge

MESSAGE_OVERHEAD = 64
"""Tokens a provider adds around one user message and its schema, counted generously."""
CONCURRENT = 8
"""Calls in flight at once on the normal API. A batch pass sends every call it can afford together."""


# --------------------------------------------------------------------------
# the pass "cheatbench": its units
# --------------------------------------------------------------------------
def run_spec(run: RunDir) -> cb_judge.JudgeSpec:
    """The spec the run's own judge would use: its task's schema, with Lucy's addendum when its config asked."""
    task = run.run_json["task"]
    addendum = judging.JUDGE_ADDENDUM if (run.run_json.get("config") or {}).get("judge_addendum") else None
    return judging.judge_spec(BY_TASK[task].task_dir, task, addendum=addendum)


def cheatbench_units(run_dir: Path) -> tuple[list[judging.Unit], list[str]]:
    """One unit per stored trajectory CheatBench would judge (core/judge.py is_judgeable: a transcript, no lost
    episode), keyed by episode; and the episodes skipped, with the reason. The trajectory is the run's stored
    transcript and the agent's instructions its "prompt", as the run's own judge read them: no eval log is read."""
    run = RunDir(run_dir).require()
    spec = run_spec(run)
    schema = judging.response_schema(spec.verdict)
    hashes = judging.spec_record(spec)
    prompt = {**judging.spec_prompt(spec), "prompt_sha256": hashes["prompt_sha256"],
              "schema_sha256": hashes["schema_sha256"]}
    units, skipped = [], []
    for ep in run.episodes():
        path = run.transcript_path(ep["episode"])
        if not path.is_file() or not cb_judge.is_judgeable(ep):
            skipped.append(f"{ep['episode']}: {'no stored transcript' if not path.is_file() else 'lost episode'}")
            continue
        identity = {"unit": ep["episode"], **{key: ep.get(key) for key in VERDICT_IDENTITY},
                    "agent": ep.get("orbit_agent"), "role": ep.get("role"),
                    "transcript_sha256": judging.sha256(path.read_bytes())}
        units.append(judging.Unit(run.path, ep["episode"], judging.prompt_text(spec, run.transcript(ep["episode"])),
                                  schema, judging.validator(spec), spec.fields, prompt, identity))
    return units, skipped


PASSES: dict[str, Callable[[Path], tuple[list[judging.Unit], list[str]]]] = {
    "cheatbench": cheatbench_units,
    "collusion": collusion_units,
}
"""Each pass's units, by the pass names verdict_files.PASSES lists."""


# --------------------------------------------------------------------------
# which runs and which units
# --------------------------------------------------------------------------
def expand(paths: list[Path]) -> list[Path]:
    """Run directories as given; a study folder (no run.json) stands for its valid runs, as the transcript viewer
    lists them (list_collection, the one place a run's validity is read). Each run once."""
    out: list[Path] = []
    for path in map(Path, paths):
        found = [path] if RunDir(path).is_run() else [r.path for r in list_collection(path) if r.status == "valid"]
        if not found:
            raise ValueError(f"neither a run nor a study with valid runs: {path}")
        out += [p for p in found if p.resolve() not in {o.resolve() for o in out}]
    return out


def todo(units: list[judging.Unit], held: dict[str, dict], *, only_missing: bool, redo: bool) -> list[judging.Unit]:
    """The units to judge given the rows already held for this judge: all with redo; with only_missing those with no
    row; else those without a parsed row."""
    if redo:
        return units
    if only_missing:
        return [u for u in units if u.key not in held]
    return [u for u in units if not (held.get(u.key) or {}).get("parse_ok")]


# --------------------------------------------------------------------------
# money
# --------------------------------------------------------------------------
def most_cost(judge: judging.Judge, unit: judging.Unit) -> float:
    """The most one call can cost: every byte of the message and of the schema as an input token, plus the model's
    max_output as output, at the judge's prices."""
    schema = len(json.dumps(unit.schema.json_schema.model_dump(exclude_none=True)).encode())
    tokens = len(unit.text.encode()) + schema + MESSAGE_OVERHEAD
    return cost_usd(judge.prices, tokens, 0, judge.max_output)


@dataclass
class Tally:
    """The pass's running account: calls made, parsed and failed, tokens, dollars spent and reserved, whether the cap
    stopped it, and the units the cap left."""
    cap: float
    calls: int = 0
    parsed: int = 0
    failed: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    spent: float = 0.0
    reserved: float = 0.0
    stopped: bool = False
    left: list[str] = field(default_factory=list)

    def summary(self) -> str:
        line = (f"== {self.calls} calls ({self.parsed} parsed, {self.failed} failed), {self.input_tokens} input "
                f"tokens, {self.output_tokens} output tokens, ${self.spent:.4f} of the ${self.cap:.2f} cap")
        if self.stopped:
            line += f"; stopped before the cap with {len(self.left)} units left"
        return line


# --------------------------------------------------------------------------
# the pass
# --------------------------------------------------------------------------
async def judge_units(units: list[judging.Unit], judge: judging.Judge, pass_name: str, tally: Tally,
                      concurrent: int) -> None:
    """Judge the units under the cap, writing each row to its run's verdict file when its call returns."""
    gate, lock = asyncio.Semaphore(concurrent), asyncio.Lock()

    async def one(n: int, unit: judging.Unit) -> None:
        async with gate:
            bound = most_cost(judge, unit)
            async with lock:
                if tally.stopped or tally.spent + tally.reserved + bound > tally.cap:
                    tally.stopped = True
                    tally.left.append(f"{unit.run_dir.name}/{unit.key}")
                    return
                tally.reserved += bound
            row = await unit.judge(judge)
            async with lock:
                tally.reserved -= bound
                usage = row["judge_usage"] or {}
                tally.calls += 1
                tally.parsed += bool(row["parse_ok"])
                tally.failed += not row["parse_ok"]
                tally.input_tokens += usage.get("input", 0)
                tally.output_tokens += usage.get("output", 0)
                tally.spent += row["judge_cost_usd"] or 0.0
                verdict_files.write(unit.run_dir, pass_name, judge.name, [row])
                cost = row["judge_cost_usd"]
                print(f"[{n}/{len(units)}] {unit.run_dir.name} {unit.key} parse_ok={row['parse_ok']} "
                      f"{'' if cost is None else f'${cost:.4f}'}"
                      f"{' ERR: ' + row['judge_error'] if row['judge_error'] else ''}", flush=True)

    await asyncio.gather(*(one(n, unit) for n, unit in enumerate(units, 1)))


def refuse_unpriced(judge: judging.Judge, cap: float | None) -> None:
    """A pass needs a positive cap and a judge whose calls can be bounded: prices and max_output."""
    if cap is None or cap <= 0:
        raise ValueError("a judge pass needs --cap, a positive number of dollars it may spend")
    if not judge.prices:
        raise ValueError(f"judge {judge.name}: its model has no price in src/models/model_registry.yaml")
    if not judge.max_output:
        raise ValueError(f"judge {judge.name}: its model has no max_output in src/models/model_registry.yaml, so no "
                         "call's cost can be bounded")


def run_pass(pass_name: str, paths: list[Path], judge: judging.Judge, *, cap: float | None, batch: bool = False,
             limit: int | None = None, only_missing: bool = False, redo: bool = False) -> Tally:
    """The pass over the runs (study folders expanded to their valid runs) with this judge, under the cap. Returns
    the tally; raises ValueError on a refused start."""
    if pass_name not in PASSES:
        raise ValueError(f"unknown judge pass {pass_name!r}; choose one of {', '.join(PASSES)}")
    if only_missing and redo:
        raise ValueError("--only-missing and --redo contradict each other")
    refuse_unpriced(judge, cap)
    units: list[judging.Unit] = []
    for run_dir in expand(paths):
        found, skipped = PASSES[pass_name](run_dir)
        held = verdict_files.by_unit(verdict_files.rows(run_dir, pass_name, judge.name))
        chosen = todo(found, held, only_missing=only_missing, redo=redo)
        units += chosen
        print(f"== {run_dir.name}: {len(found)} units, {len(found) - len(chosen)} held, {len(chosen)} to judge"
              + (f"; skipped {'; '.join(skipped)}" if skipped else ""), flush=True)
    if limit is not None:
        units = units[:limit]
    tally = Tally(cap)
    print(f"== {pass_name} ({judge.name}, {'batch' if batch else 'normal API'}): {len(units)} calls, "
          f"cap ${cap:.2f}", flush=True)
    asyncio.run(judge_units(units, judge, pass_name, tally, max(len(units), 1) if batch else CONCURRENT))
    print(tally.summary(), flush=True)
    return tally
