"""Claude straight at Anthropic, as safe as through OpenRouter. A provider's key file holds the process's key for that
provider, over the environment's value; a file open to others, an empty one or a key with whitespace inside is refused
without showing the key; the key never reaches what a run records. A direct entry is priced, its long-prompt tier
included, and capped like any paid model. A run that mixes routes gets its key, its cache gate and its price for every
model of every role. No test makes a call: every provider is a fake or a client that is never asked."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from inspect_ai.model import ModelCost, ModelOutput, ModelUsage, get_model
from inspect_ai.model._model_info import _get_model_info_direct, clear_model_info_cache

from src import run
from src.configuration import launch_command
from src.configuration.presets import build_config
from src.domains.protein import ProteinDesign
from src.models import claude_prompt_cache as cache
from src.models import cost_cap
from src.models import model_registry as models
from src.tasks import swarm_task

KEY = "sk-ant-made-up-key-0123456789"
OLDER = "sk-ant-the-environments-older-key"
OPUS, HAIKU = "anthropic/claude-opus-5-5", "anthropic/claude-haiku-5-5"
DIRECT = ["claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"]
QWEN = "qwen3.8-flash-openrouter"


@pytest.fixture(autouse=True)
def env(monkeypatch):
    """A private copy of the environment holding an older Anthropic key, as ~/.zshrc exports one."""
    monkeypatch.setattr(os, "environ", dict(os.environ))
    os.environ["ANTHROPIC_API_KEY"] = OLDER
    os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)
    clear_model_info_cache()
    yield os.environ
    clear_model_info_cache()


@pytest.fixture
def key_file(monkeypatch, tmp_path) -> Path:
    """Where Anthropic's key file is looked for, still absent."""
    path = tmp_path / "anthropic_key"
    monkeypatch.setattr(models, "key_path", lambda provider: path if provider == "anthropic" else None)
    return path


def write(path: Path, text: str, mode: int = 0o600) -> Path:
    path.write_text(text)
    path.chmod(mode)
    return path


# ---------------------------------------------------------------------------------------------- 1. the key file
def test_only_anthropic_names_a_key_file_and_it_sits_in_the_config_folder():
    named = {name: route["key_file"] for name, route in models.providers().items() if route.get("key_file")}
    assert named == {"anthropic": "~/.config/spar-guardian-agents/anthropic_key"}
    assert Path(named["anthropic"]).expanduser() == Path.home() / ".config/spar-guardian-agents/anthropic_key"


def test_the_key_file_is_the_key_of_this_process_over_the_environments(key_file):
    write(key_file, KEY + "\n")
    models.prepare_env(OPUS)
    assert os.environ["ANTHROPIC_API_KEY"] == KEY
    assert models.key_source(OPUS) == f"the key file {key_file}"
    assert get_model(OPUS, memoize=False).api.api_key == KEY     # the client Inspect builds sends the file's key


def test_without_the_key_file_the_environments_key_serves_as_before(key_file):
    models.prepare_env(OPUS)
    assert os.environ["ANTHROPIC_API_KEY"] == OLDER and models.key_source(OPUS) == "ANTHROPIC_API_KEY"
    del os.environ["ANTHROPIC_API_KEY"]
    with pytest.raises(ValueError, match=f"ANTHROPIC_API_KEY must be set .* written to {key_file}"):
        models.prepare_env(OPUS)
    assert models.key_source(OPUS) is None


@pytest.mark.parametrize(("text", "mode", "why"), [
    ("", 0o600, "is empty"),
    ("  \n", 0o600, "is empty"),
    ("sk-ant-secretpart other\n", 0o600, "no whitespace inside"),
    ("sk-ant-secretpart\nsk-ant-second\n", 0o600, "no whitespace inside"),
    ("sk-ant-secretpart\n", 0o640, "chmod 600"),
    ("sk-ant-secretpart\n", 0o604, "chmod 600"),
])
def test_a_bad_key_file_is_refused_without_showing_the_key(key_file, text, mode, why):
    write(key_file, text, mode)
    with pytest.raises(ValueError, match=why) as refusal:
        models.prepare_env(OPUS)
    assert "secretpart" not in str(refusal.value) and str(key_file) in str(refusal.value)
    assert os.environ["ANTHROPIC_API_KEY"] == OLDER


def test_a_variable_inspect_would_send_in_place_of_the_key_is_refused(key_file):
    write(key_file, KEY)
    os.environ["ANTHROPIC_AUTH_TOKEN"] = "made-up-token"
    with pytest.raises(ValueError, match="unset ANTHROPIC_AUTH_TOKEN"):
        models.prepare_env(OPUS)


def test_a_run_refuses_a_bad_key_file_and_the_judge_takes_its_key_from_the_file(key_file):
    write(key_file, "sk-ant-secretpart\n", 0o644)
    with pytest.raises(run.Refusal, match="chmod 600"):
        run.require_model(OPUS)
    with pytest.raises(run.Refusal, match="chmod 600"):
        run.require_judge_key("claude-haiku-5-5", 2)
    write(key_file, KEY)
    run.require_judge_key("claude-haiku-5-5", 2)
    assert os.environ["ANTHROPIC_API_KEY"] == KEY


def test_the_key_never_reaches_the_launch_or_the_config_a_run_records(monkeypatch, key_file):
    write(key_file, KEY)
    models.prepare_env(OPUS)
    monkeypatch.setattr(launch_command, "_LAUNCH", None)
    launch_command.record("src", ["run", "--set", "regulator_model=claude-opus-5-5", f"api_key={KEY}", KEY])
    config = build_config("default", "small", ["regulators=1", "regulator_model=claude-opus-5-5"])
    recorded = swarm_task(ProteinDesign(), config).dataset[0].metadata
    cb = recorded["cheatbench"]
    assert cb["config"]["regulator_model"] and cb["launch"]["argv"][-1] == launch_command.REDACTED
    assert KEY not in json.dumps(recorded, default=str)


# ---------------------------------------------------------------------------------------------- 4. the cap
@pytest.mark.parametrize("name", DIRECT)
def test_a_direct_entry_is_paid_priced_and_capped_like_any_paid_model(name):
    model = models.resolve(name)
    assert cost_cap.is_paid(model) and cost_cap.model_cost(name) is not None
    assert cost_cap.cap_for([model, "mockllm/model"], 5.0) == 5.0
    for lookup in cost_cap.lookup_names(model):
        assert _get_model_info_direct(get_model(lookup, memoize=False, api_key="made-up")).cost \
            == cost_cap.model_cost(name), lookup
    for cap in (None, 0, -1):
        with pytest.raises(ValueError, match="positive cost_limit_usd"):
            cost_cap.cap_for([model], cap)


def test_haiku_at_anthropic_bills_a_call_past_its_line_at_the_long_prompt_rates():
    below = ModelUsage(input_tokens=50_000, output_tokens=10, total_tokens=50_010)
    over = ModelUsage(input_tokens=1_000, input_tokens_cache_read=100_000, output_tokens=10, total_tokens=101_010)
    assert cost_cap.call_cost(HAIKU, below) == ModelCost(input=0.10, output=0.50, input_cache_read=0.01,
                                                         input_cache_write=0.125)
    assert cost_cap.call_cost(HAIKU, over) == ModelCost(input=0.50, output=2.50, input_cache_read=0.05,
                                                        input_cache_write=0.625)
    assert cost_cap.call_cost(HAIKU, over, batch=True) == ModelCost(input=0.25, output=1.25, input_cache_read=0.025,
                                                                    input_cache_write=0.3125)
    assert cost_cap.model_cost(HAIKU) == cost_cap.call_cost(HAIKU, over)    # the fallback takes the higher tier


@pytest.mark.parametrize("name", [HAIKU, "swarm/claude-haiku-5-5"])
def test_each_call_of_a_direct_haiku_is_priced_at_its_own_tier(name):
    model = cost_cap.price_calls(get_model(name, memoize=False, api_key="made-up"))
    output = ModelOutput.from_content("claude-haiku-5-5", "ok")
    output.usage = ModelUsage(input_tokens=120_000, output_tokens=10, total_tokens=120_010)
    [served] = model.api.served_model_usage(output)
    assert served.cost == ModelCost(input=0.50, output=2.50, input_cache_read=0.05, input_cache_write=0.625)


# ---------------------------------------------------------------------------------------------- 5. mixed routes
class Started(Exception):
    """The eval stand-in stops the run here."""


ROLES = ["regulators=1", "regulator_model=claude-opus-5-5", "disregulators=1",
         "disregulator_model=claude-sonnet-5-5", f"worker_model={QWEN}"]


def mixed_run(monkeypatch, tmp_path, proven: bool) -> tuple[int | None, dict, list[str]]:
    """run.run with Qwen through OpenRouter as the run's model and its workers', and Claude at Anthropic as the
    regulator's and the disregulator's: its exit code (None when it reached the eval), the eval's arguments and every
    model prepare_env saw."""
    os.environ["OPENROUTER_API_KEY"] = "made-up"
    seen: dict = {}
    prepared: list[str] = []
    prepare_env, entry_of = models.prepare_env, models.entry_of

    def eval_stub(task, **kwargs):
        seen.update(kwargs)
        raise Started

    def recorded(name):
        entry = entry_of(name)
        if proven and entry and cache.claude_route(entry["inspect"]):
            entry = {**entry, "caching_verified": {"date": "2026-11-25", "inspect": cache.installed_inspect()}}
        return entry

    monkeypatch.setattr(models, "prepare_env", lambda model: prepared.append(model) or prepare_env(model))
    monkeypatch.setattr(models, "entry_of", recorded)
    monkeypatch.setattr(run.inspect_ai, "eval", eval_stub)
    monkeypatch.setattr(run.settings, "LOGS", tmp_path)
    monkeypatch.setattr(run, "access_line", lambda task: "== access")
    config = build_config("default", "small", ["judge=none", *ROLES])
    try:
        code = run.run("x", ProteinDesign(), config, models.resolve(QWEN), None, task=object())
    except Started:
        code = None
    return code, seen, prepared


def test_a_mixed_run_takes_each_models_key_and_is_refused_until_each_claudes_cache_is_proven(monkeypatch, tmp_path,
                                                                                              key_file, capsys):
    write(key_file, KEY)
    code, seen, prepared = mixed_run(monkeypatch, tmp_path, proven=False)
    assert code == 2 and not seen
    assert {models.resolve(m) for m in prepared} >= {models.resolve(QWEN), OPUS, "anthropic/claude-sonnet-5-5"}
    assert "Claude at Anthropic is refused until scripts/check_anthropic_cache.py" in capsys.readouterr().err
    assert os.environ["ANTHROPIC_API_KEY"] == KEY


def test_a_proven_mixed_run_reaches_the_eval_with_one_cap_and_a_price_for_every_model(monkeypatch, tmp_path,
                                                                                       key_file):
    write(key_file, KEY)
    code, seen, _ = mixed_run(monkeypatch, tmp_path, proven=True)
    assert code is None and seen["cost_limit"] == 5.0
    for name in (QWEN, "claude-opus-5-5", "claude-sonnet-5-5"):
        for lookup in cost_cap.lookup_names(models.resolve(name)):
            assert _get_model_info_direct(get_model(lookup, memoize=False, api_key="made-up")).cost \
                == cost_cap.model_cost(name), lookup
