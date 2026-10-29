"""Paths, model names and fixed settings shared by the whole of src/.

Import the names you need: `from src.configuration import settings` or
`from src.configuration.settings import ROOT, DEFAULT_MODEL`. Model-facing text lives in
`src/configuration/prompts.yaml` and `src/configuration/instruction_levels.yaml`; the control criteria and patterns in
`src/configuration/controls.yaml`; the models we can consider, with their routes, keys and prices, in
`src/configuration/model_registry.yaml` (read through model_registry.py).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent          # src/configuration/
ROOT = HERE.parents[1]                           # the repository root
RUNS = ROOT / "runs"
LOGS = ROOT / "logs"
BUILD = ROOT / "build"
CHEATBENCH = ROOT / "submodules" / "cheatbench"
ORBIT = ROOT / "submodules" / "orbit"
LUCY = ROOT / "submodules" / "lucy"
PROMPTS = HERE / "prompts.yaml"
CONTROLS = HERE / "controls.yaml"
INSTRUCTION_LEVELS = HERE / "instruction_levels.yaml"
MODELS = HERE / "model_registry.yaml"

# The agents' model, an Inspect model string (model_registry.yaml lists every alternative), and the judge's entry in
# JUDGE_MODELS_CONFIG, whose model string model_registry.yaml prices. gpt-6-luna as judge since 2026-10-28: twenty times
# cheaper than gpt-6-sol, CheatBench's default, and it agreed with it on 266 of 269 judged episodes (claude-haiku-5-5,
# the same price, on 222); gpt-6-sol stays in JUDGE_MODELS_CONFIG as the reference judge.
DEFAULT_MODEL = "openrouter/deepseek/deepseek-v4.1-flash-1017"
DEFAULT_MONITOR_MODEL = DEFAULT_MODEL
JUDGE_MODEL = "gpt-6-luna"
JUDGE_MODELS_CONFIG = HERE.parent / "benchmark" / "judge_models.yaml"
UI_PORT = 8765
BOARD_ID = "board"    # the shared channel's id (src/swarm/board_config.py, src/swarm/turn_rules/, the SWE prompts)
BOARD_TOOLS = ("board_status", "board_read", "board_post")    # the board's tools (src/swarm/pull_board.py)
MODEL_TIMEOUT_S = 2700
# Each domain's turn budget per agent: the working steps every agent of a swarm gets at every scale
# (SwarmConfig.turn_budget, SwarmConfig.steps_for), the same on every domain. CheatBench bounds an episode at 150
# harness turns (run.py --max-turns) and the task's wall clock, never told to the agent; ours is two thirds of that,
# since agents stop on their own (GPT-6 Luna alone: 11 to 14 turns on openmath, 25 to 44 on SWE, 35 to 41 on
# knowledge work, 2026-10-27) and the cap only bounds the stragglers. It is twice the latest first attempt at the
# exploit seen in the Gemini sweep (turn 45 of 361 agents), so it cuts no cheating the way a solo median did. A run
# changes it with `--set turn_budget=120` or `--set turn_budget=knowledge_work:70`.
TURN_BUDGETS: dict[str, int] = {"openmath": 100, "swe": 100, "knowledge_work": 100, "subset_sum": 100,
                                "prime_factorization": 100, "protein_design": 100}

# Seconds Inspect's hard time limit sits above the scheduler's soft halt (SwarmConfig.time_grace_s default): ORBIT
# halts between turns at time_limit_s, so a step in flight finishes and its usage is logged; the hard limit cuts only
# a run whose step never returns.
HARD_TIME_GRACE_S = 600

# The verdict fields the command line prints for each judged episode (openmath_agent's Verdict and the shared rule).
VERDICT_KEYS = (
    "parse_ok", "cheated", "honey_pots_seen", "attempted_access", "extracted_submission",
    "success_claim_seen_before_access", "disclosed_access", "explicit_prohibition_violated",
    "judge_cost_usd", "judge_error",
)

# Episode failure labels that mean the run is incomplete: a harness crash or a provider error.
INCOMPLETE_FAILURES = ("harness", "connection_closed")

# The submodule commits this code was written against. Task construction refuses any other head.
EXPECTED_SUBMODULE_HEADS = {"cheatbench": "4d1a825", "orbit": "588b303", "lucy": "68a1199"}


def load_prompts() -> dict:
    return yaml.safe_load(PROMPTS.read_text())


def load_controls() -> dict:
    return yaml.safe_load(CONTROLS.read_text())


def load_instruction_levels() -> dict:
    return yaml.safe_load(INSTRUCTION_LEVELS.read_text())


def load_models() -> dict:
    return yaml.safe_load(MODELS.read_text())


def submodule_head(name: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(ROOT / "submodules" / name), "rev-parse", "--short", "HEAD"], text=True,
    ).strip()


def code_state() -> dict:
    """The code a run uses, for run.json "code": the repository's short commit, whether src, tests, examples or
    scripts carry uncommitted changes, and every submodule's head; None where git cannot say."""
    def git(*args: str) -> str:
        return subprocess.check_output(["git", "-C", str(ROOT), *args], text=True).strip()
    try:
        commit = git("rev-parse", "--short", "HEAD")
        dirty = bool(git("status", "--porcelain", "--untracked-files=no", "--ignore-submodules", "--",
                         "src", "tests", "examples", "scripts"))
    except Exception:  # noqa: BLE001 - no git, no claim
        commit, dirty = None, None
    heads: dict[str, str | None] = {}
    for sub in sorted(p.name for p in (ROOT / "submodules").iterdir() if p.is_dir()):
        try:
            heads[sub] = submodule_head(sub)
        except Exception:  # noqa: BLE001
            heads[sub] = None
    return {"commit": commit, "dirty": dirty, "submodules": heads}


def check_submodule_heads() -> None:
    for name, expected in EXPECTED_SUBMODULE_HEADS.items():
        head = submodule_head(name)
        if not head.startswith(expected):
            raise RuntimeError(f"submodules/{name} is at {head}; this code was written against {expected}")
