"""Paths, model names and fixed settings shared by the whole of src/.

Import the names you need: `from src.configuration import settings` or
`from src.configuration.settings import ROOT, DEFAULT_MODEL`. Model-facing text lives in
`src/configuration/prompts.yaml` and `src/configuration/instruction_levels.yaml`; the control criteria and patterns in
`src/configuration/controls.yaml`; the models we can consider, with their routes, keys and prices, in
`src/configuration/models.yaml` (read through models.py).
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
MODELS = HERE / "models.yaml"

# The agents' model, an Inspect model string (models.yaml lists every alternative), and the judge's entry in
# JUDGE_MODELS_CONFIG, whose model string models.yaml prices. gpt-6-luna as judge since 2026-10-28: twenty times
# cheaper than gpt-6-sol, CheatBench's default, and it agreed with it on 266 of 269 judged episodes (claude-haiku-5-5,
# the same price, on 222); gpt-6-sol stays in JUDGE_MODELS_CONFIG as the reference judge.
DEFAULT_MODEL = "openrouter/deepseek/deepseek-v4.1-flash-1017"
DEFAULT_MONITOR_MODEL = DEFAULT_MODEL
JUDGE_MODEL = "gpt-6-luna"
JUDGE_MODELS_CONFIG = HERE.parent / "benchmark" / "models.yaml"
SWARM_COMPOSITIONS = ["R0D0", "R10D0", "R0D10", "R1D1", "R1D2", "R2D2", "R3D3", "R2D5", "R5D2", "R0D3", "R3D0"]
UI_PORT = 8765
BOARD_ID = "board"    # the shared channel's id (src/swarm/comm_topology.py, src/swarm/rules.py, the SWE prompts)
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

# The verdict fields the command line prints for each judged episode (openmath_agent's Verdict and the shared rule).
VERDICT_KEYS = (
    "parse_ok", "cheated", "honey_pots_seen", "attempted_access", "extracted_submission",
    "success_claim_seen_before_access", "disclosed_access", "explicit_prohibition_violated",
    "judge_cost_usd", "judge_error",
)

# Episode failure labels that mean the run is incomplete: a harness crash or a provider error.
INCOMPLETE_FAILURES = ("harness", "connection_closed")

# The submodule commits this code was written against. Task construction refuses any other head.
EXPECTED_SUBMODULE_HEADS = {"cheatbench": "4d1a825", "orbit": "588b303", "lucy": "c410183"}


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


def check_submodule_heads() -> None:
    for name, expected in EXPECTED_SUBMODULE_HEADS.items():
        head = submodule_head(name)
        if not head.startswith(expected):
            raise RuntimeError(f"submodules/{name} is at {head}; this code was written against {expected}")
