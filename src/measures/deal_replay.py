"""Whether today's code deals a run the roster and the holdings it recorded.

    replay(run_json) -> Replay("same", "")      # or "differs" or "refused", with what differs; None for a task not
                                                # replayed

A run records its roster (run.json "agents") and what each agent held (run.json "access" agents' holds). The replay
rebuilds the run's config from run.json "config": the preset and the scale, then the fields that fix the deal (the
team, the roles, the seed, the name the IDs are drawn from, the access policy and the mode). It plans the run with
today's domain class and the hidden roles a run gets, then compares. A deal that differs means today's code would
give other agents or other holdings: a measure that plans the run again, such as a collaboration test, does not
describe it. A deal today's code refuses means the config no longer runs at all.

The replay covers protein design, creative writing and SWE (REPLAYED), the tasks of study_exploration_1. It needs no
sandbox and no model.
"""
from __future__ import annotations

from dataclasses import dataclass

from src import tasks
from src.configuration.presets import build_config
from src.configuration.swarm_config import SwarmConfig
from src.domains import BY_TASK
from src.domains.benchmark_protocol import Benchmark

REPLAYED = ("protein_design", "creative_writings", "software_engineering")
"""The CheatBench tasks the replay covers."""
DEAL_FIELDS = ("n_agents", "regulators", "disregulators", "seed", "id_salt", "access", "mode")
"""The config fields that fix the roster, the roles and the deal, set over the preset and the scale."""


@dataclass(frozen=True)
class Replay:
    """What today's code deals a run, against what the run recorded."""

    status: str
    """same, differs or refused."""
    detail: str = ""
    """What differs, or why today's plan refused the config; "" when the deal is the same."""


def config_of(run_json: dict) -> SwarmConfig:
    """The run's config as the deal reads it: run.json config's preset and scale, then its DEAL_FIELDS."""
    config = run_json["config"]
    fields = {key: config[key] for key in DEAL_FIELDS if key in config}
    return build_config(config.get("preset") or "default", config.get("scale")).replace(**fields)


def benchmark_of(run_json: dict) -> Benchmark:
    """The run's domain class on its row (the block's row_id, or the SWE issue), with the options its block records."""
    cls = BY_TASK[run_json["task"]]
    block = run_json[cls.block]
    row = block.get("row_id") or block.get("issue")
    return cls(row, **{option: block[option] for option in cls.options if option in block})


def replay(run_json: dict) -> Replay | None:
    """Today's deal of the run against its record; None for a task outside REPLAYED."""
    if run_json.get("task") not in REPLAYED:
        return None
    try:
        config = config_of(run_json)
        plan = benchmark_of(run_json).plan(config, tasks.hidden_roles(config))
    except (ValueError, NotImplementedError) as error:
        return Replay("refused", str(error))
    roster = sorted(run_json.get("agents") or [])
    if plan.agents != roster:
        return Replay("differs", f"roster: recorded {', '.join(roster)}; today {', '.join(plan.agents)}")
    recorded = {agent: sorted(entry.get("holds") or [])
                for agent, entry in ((run_json.get("access") or {}).get("agents") or {}).items()}
    today = {agent: sorted(items) for agent, items in plan.holds.items()}
    changed = [agent for agent in roster if recorded.get(agent) != today.get(agent)]
    if changed:
        return Replay("differs", "holdings: " + "; ".join(
            f"{agent} recorded {'+'.join(recorded.get(agent) or []) or 'nothing'}, "
            f"today {'+'.join(today.get(agent) or []) or 'nothing'}" for agent in changed))
    return Replay("same")
