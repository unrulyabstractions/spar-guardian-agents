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
# JUDGE_MODELS_CONFIG, whose model string models.yaml prices. claude-haiku-5-5 since 2026-10-28: twenty times
# cheaper than gpt-6-sol, CheatBench's default, which stays in JUDGE_MODELS_CONFIG as the reference judge.
DEFAULT_MODEL = "openai/gpt-6-luna"
DEFAULT_MONITOR_MODEL = DEFAULT_MODEL
JUDGE_MODEL = "claude-haiku-5-5"
JUDGE_MODELS_CONFIG = HERE.parent / "benchmark" / "models.yaml"
SWARM_COMPOSITIONS = ["R0D0", "R10D0", "R0D10", "R1D1", "R1D2", "R2D2", "R3D3", "R2D5", "R5D2", "R0D3", "R3D0"]
UI_PORT = 8765
BOARD_ID = "board"    # the shared channel's id (src/swarm/comm_topology.py, src/swarm/rules.py, the SWE prompts)
MODEL_TIMEOUT_S = 2700
# Each domain's turn budget per agent: the turns one agent of the same model needed on the task alone, which every
# agent of a swarm gets as working steps (SwarmConfig.turn_budget, SwarmConfig.steps_for). The medians of three lone
# GPT-6 Luna runs on 2026-10-27: openmath 11, 13, 14 (runs/om_lone_1..3); swe 25, 25, 44 (runs/swe_lone_1..3);
# knowledge_work 38, 41, 35 (runs/kw_lone_1..3). Measure again with lone runs (`--set n_agents=1
# --set turn_budget=none --set steps_per_agent=200`) and `python -m src budget runs/<lone> ...`; a run changes any of
# them with `--set turn_budget=knowledge_work:70`.
TURN_BUDGETS: dict[str, int] = {"openmath": 13, "swe": 25, "knowledge_work": 38}

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
