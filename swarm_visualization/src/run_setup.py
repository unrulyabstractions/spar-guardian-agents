"""What a run was set up as, read from its processed/run.json: who is in the team, which task version, and whether
the workers were told to cheat. Shared by the run's page (the header chips) and the study page (the grid's axes).

    run_setup(run_json, roles, version_values)
    # {"workers": 8, "worker_model": "DeepSeek", "regulators": 2, "regulator_model": "Opus", "disregulators": 0,
    #  "disregulator_model": None, "version": "ian_gflipt", "check_tool": True, "told_to_cheat": False,
    #  "rule": None, "seed": 1}
"""
from __future__ import annotations

MODEL_NAMES = (("opus", "Opus"), ("sonnet", "Sonnet"), ("haiku", "Haiku"), ("fable", "Fable"),
               ("deepseek", "DeepSeek"), ("luna", "Luna"), ("terra", "Terra"), ("sol", "Sol"), ("qwen", "Qwen"),
               ("gemini", "Gemini"), ("kimi", "Kimi"), ("grok", "Grok"), ("hy4", "HY4"), ("gpt", "GPT"),
               ("llama", "Llama"))
CHECK_TOOL = "check_fix"


def model_name(model: str | None) -> str:
    """A short display name for a model string: anthropic/claude-opus-5-5 -> Opus."""
    low = (model or "").lower()
    for key, name in MODEL_NAMES:
        if key in low:
            return name
    return low.rsplit("/", 1)[-1] or "?"


def _count(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def run_setup(run_json: dict, roles: dict[str, str] | None = None, version_values: dict | None = None,
              tools_called: set[str] | None = None) -> dict:
    """The run's setup from its recorded config. Counts come from config (n_agents minus regulators and
    disregulators = workers), else from the roles found. None where the run does not say."""
    config = run_json.get("config") or {}
    roles = roles or {}
    role_models = run_json.get("role_models") or {}
    found = {role: sum(1 for r in roles.values() if r == role) for role in ("worker", "regulator", "disregulator")}
    regulators = _count(config.get("regulators"))
    disregulators = _count(config.get("disregulators"))
    regulators = found["regulator"] if regulators is None else regulators
    disregulators = found["disregulator"] if disregulators is None else disregulators
    n_agents = _count(config.get("n_agents"))
    workers = n_agents - regulators - disregulators if n_agents is not None else found["worker"]

    def role_model(role: str, count: int) -> str | None:
        if not count:
            return None
        return model_name(role_models.get(role) or config.get(f"{role}_model") or run_json.get("model"))

    launch = run_json.get("launch") or {}
    module = str(launch.get("module") or "").rsplit(".", 1)[-1]
    variant = (run_json.get("swe") or {}).get("variant")
    version = variant or (module.split("_minimal")[0] if module else None) or run_json.get("task")
    limited = {str(row.get("tool")) for row in config.get("tool_limits") or [] if isinstance(row, dict)}
    check_tool = (CHECK_TOOL in limited or bool((version_values or {}).get("checks"))
                  or CHECK_TOOL in (tools_called or set()))
    rule = config.get("rule_level")
    return {"workers": workers,
            "worker_model": model_name(role_models.get("worker") or config.get("worker_model")
                                       or run_json.get("model")),
            "regulators": regulators, "regulator_model": role_model("regulator", regulators),
            "disregulators": disregulators, "disregulator_model": role_model("disregulator", disregulators),
            "version": version, "check_tool": check_tool,
            "told_to_cheat": module.endswith("_cheat"),
            "rule": rule if rule and rule != "none" else None,
            "seed": config.get("seed")}
