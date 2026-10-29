"""ORBIT's shipped defenses as named controls, as configuration values.

    defenses_for("guardian_task", monitor_model="google/gemini-3.8-flash") -> [DefenseConfig]

Two families. "Stock" controls use the defense exactly as ORBIT ships it (generic security criteria, default prompts
and patterns). "Task" controls carry CheatBench's rule for the openmath task, phrased without naming the planted
branch: reading another agent's submission is the thing to stop. Their criteria and regex patterns live in
src/configuration/controls.yaml.

ORBIT enforces every control at the model and tool boundaries (orbit/defenses/boundaries.py). A blocked tool call
raises a ToolError the model sees. A blocked model input is withheld from the provider only. A blocked completion is
replaced by a refusal with no tool calls. When a refusal is the last message, rules.py TurnRulesFilter appends the
continue prompt to the next provider input, for every run.

Not offered here: file_change_monitor, git_branch_isolation, code_review_defense and diff_monitor, which read
SWE-bench issue metadata from the experiment config.
"""
from __future__ import annotations

from orbit.configs.defense import DefenseConfig

from src.configuration.settings import DEFAULT_MONITOR_MODEL, load_controls

BASELINE = ("", "baseline", "none")


def _llm(name: str, defense_type: str, monitor_model: str, judge_prompt: str | None) -> DefenseConfig:
    config = {"judge_prompt": judge_prompt} if judge_prompt else {}
    return DefenseConfig(name=name, defense_type=defense_type, model_override=monitor_model, config=config)


def _catalog(monitor_model: str) -> dict[str, list[DefenseConfig]]:
    controls = load_controls()
    criteria = controls["criteria"]["openmath"]
    patterns = controls["patterns"]
    return {
        # stock: the defense as shipped
        "vaccination": [DefenseConfig(name="vaccination", defense_type="prompt_vaccination")],
        "secure_model": [DefenseConfig(name="secure_model", defense_type="secure_model")],
        "tool_wrapper": [DefenseConfig(name="tool_wrapper", defense_type="tool_wrapper")],
        "dual_llm": [DefenseConfig(name="dual_llm", defense_type="dual_llm", model_override=monitor_model)],
        "monitor_stock": [_llm("monitor_stock", "llm_monitor", monitor_model, None)],
        "guardian_stock": [_llm("guardian_stock", "guardian_agent", monitor_model, None)],
        # task-aware: CheatBench's rule as criteria or patterns
        "monitor_task": [_llm("monitor_task", "llm_monitor", monitor_model, criteria)],
        "guardian_task": [_llm("guardian_task", "guardian_agent", monitor_model, criteria)],
        "regex_task": [DefenseConfig(name="regex_task", defense_type="monitor",
                                     config={"blocked_patterns": patterns["openmath"]})],
        "regex_task_hard": [DefenseConfig(name="regex_task_hard", defense_type="monitor",
                                          config={"blocked_patterns": patterns["openmath_hard"]})],
    }


def defenses_for(control: str, monitor_model: str = DEFAULT_MONITOR_MODEL) -> list[DefenseConfig]:
    """The DefenseConfig list for one named control; "", "baseline" or "none" means no defense."""
    if control in BASELINE:
        return []
    catalog = _catalog(monitor_model)
    if control not in catalog:
        raise ValueError(f"unknown control {control!r}; choose one of {sorted(catalog)}")
    return catalog[control]
