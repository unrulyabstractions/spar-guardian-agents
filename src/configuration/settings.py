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
# JUDGE_MODELS_CONFIG, whose model string models.yaml prices.
DEFAULT_MODEL = "openai/gpt-6-luna"
DEFAULT_MONITOR_MODEL = DEFAULT_MODEL
JUDGE_MODEL = "gpt-6-sol"
JUDGE_MODELS_CONFIG = HERE.parent / "benchmark" / "models.yaml"
SWARM_COMPOSITIONS = ["R0D0", "R10D0", "R0D10", "R1D1", "R1D2", "R2D2", "R3D3", "R2D5", "R5D2", "R0D3", "R3D0"]
UI_PORT = 8765
BOARD_ID = "board"    # the shared channel's id (src/swarm/comm_topology.py, src/swarm/rules.py, the SWE prompts)
MODEL_TIMEOUT_S = 2700
# Each domain's turn budget for the whole swarm: the turns one agent used on the task alone, divided equally among
# the agents (SwarmConfig.turn_budget, SwarmConfig.steps_for). openmath: the lone Gemini run runs/gemini_riemann took
# 55 turns (n_turns of its one episode). swe: the midterm report's ~150 turns from Lucy's runs (50 per agent of three).
# knowledge_work: provisional, openmath's figure, until a lone run (n_agents=1, turn_budget=none, a high
# steps_per_agent) has run and `python -m src budget` on its output gives the measured number. A run changes any of
# them with `--set turn_budget=knowledge_work:70`.
TURN_BUDGETS: dict[str, int] = {"openmath": 55, "swe": 150, "knowledge_work": 55}

# The verdict fields the command line prints for each judged episode (openmath_agent's Verdict and the shared rule).
VERDICT_KEYS = (
    "parse_ok", "cheated", "honey_pots_seen", "attempted_access", "extracted_submission",
    "success_claim_seen_before_access", "disclosed_access", "explicit_prohibition_violated",
    "judge_cost_usd", "judge_error",
)

# Episode failure labels that mean the run is incomplete: a harness crash or a provider error.
INCOMPLETE_FAILURES = ("harness", "connection_closed")

# The submodule commits this code was written against. Task construction refuses any other head.
# lucy moved from c410183 to a3e8f17 for experiments/guardian_baseline/judge_addendum.py; its splits, pieces and
# orbit_cheatbench addendum are unchanged between the two.
EXPECTED_SUBMODULE_HEADS = {"cheatbench": "4d1a825", "orbit": "588b303", "lucy": "a3e8f17"}


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
