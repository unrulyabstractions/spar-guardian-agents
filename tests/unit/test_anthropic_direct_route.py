"""Claude straight at Anthropic, as safe as through OpenRouter. A provider's key file holds the process's key for that
provider, over the environment's value; a file open to others, an empty one or a key with whitespace inside is refused
without showing the key; the key never reaches what a run records. A direct entry is priced, its long-prompt tier
included, and capped like any paid model. A run that mixes routes gets its key, its cache gate and its price for every
model of every role. No test makes a call: every provider is a fake or a client that is never asked."""
from __future__ import annotations

import asyncio
import contextvars
import json
import os
import re
from pathlib import Path

import httpx2
import pytest
from inspect_ai.model import ChatMessageUser, GenerateConfig, ModelCost, ModelOutput, ModelUsage, get_model
from inspect_ai.model._model import init_active_model, init_model_roles
from inspect_ai.model._model_info import _get_model_info_direct, clear_model_info_cache

from scripts import check_anthropic_cache as check
from src import run, tasks
from src.configuration import launch_command
from src.configuration.presets import build_config
from src.domains.protein import ProteinDesign
from src.judging import cheatbench_judge as judging
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
    assert models.key_source(OPUS) == str(key_file)
    assert get_model(OPUS, memoize=False).api.api_key == KEY     # the client Inspect builds sends the file's key


@pytest.mark.parametrize("absent", ["missing", "dangling link", "folder"])
def test_without_its_key_file_a_run_is_refused_and_never_falls_back_to_the_environments_key(key_file, absent):
    if absent == "dangling link":
        key_file.symlink_to(key_file.parent / "gone")
    elif absent == "folder":
        key_file.mkdir()
    remedy = f"write the key to {key_file} (chmod 600)"
    with pytest.raises(ValueError, match=re.escape(remedy)):
        models.prepare_env(OPUS)
    with pytest.raises(run.Refusal, match=re.escape(remedy)):
        run.require_model(OPUS)
    for judge_command in (1, 2):
        with pytest.raises(run.Refusal, match=re.escape(remedy)):
            run.require_judge_key("claude-haiku-5-5", judge_command)
    with pytest.raises(ValueError, match=re.escape(remedy)):
        get_model("swarm/claude-opus-5-5", memoize=False)          # a role model, on any path that builds one
    assert os.environ["ANTHROPIC_API_KEY"] == OLDER and models.key_source(OPUS) == str(key_file)


@pytest.mark.parametrize(("content", "mode"), [
    (b"", 0o600), (b"\n", 0o600), (b"  \n", 0o600), (b"sk-ant-secretpart other\n", 0o600),
    (b"sk-ant-secretpart\nsk-ant-second\n", 0o600), (b"sk-ant-secretpart\n\n", 0o600), (b" sk-ant-secretpart", 0o600),
    (b"sk-ant-secretpart\r\n", 0o600), (b"sk-ant-secretpart\xc3\xa9", 0o600), (b"sk-ant-secretpart\xff", 0o600),
    (b"sk-ant-secretpart\n", 0o640), (b"sk-ant-secretpart\n", 0o604), (b"sk-ant-secretpart\n", 0o000),
])
def test_a_bad_key_file_is_refused_naming_only_its_path(key_file, content, mode):
    key_file.write_bytes(content)
    key_file.chmod(mode)
    with pytest.raises(ValueError) as refusal:
        models.prepare_env(OPUS)
    assert str(key_file) in str(refusal.value) and "secretpart" not in str(refusal.value)
    error = refusal.value          # no chained error that could show the bytes
    assert error.__cause__ is None and (error.__context__ is None or error.__suppress_context__)
    assert os.environ["ANTHROPIC_API_KEY"] == OLDER


def test_a_folder_given_as_a_key_file_is_a_value_error_naming_its_path(tmp_path):
    with pytest.raises(ValueError, match=re.escape(f"{tmp_path} cannot be read")):
        models.read_key_file(tmp_path)


@pytest.mark.parametrize("text", [KEY, KEY + "\n", "sk-ant-api03-AbC_d-9xY"])
def test_a_key_of_letters_digits_dashes_and_underscores_is_taken_with_its_final_newline_stripped(key_file, text):
    write(key_file, text)
    assert models.read_key_file(key_file) == text.removesuffix("\n")


@pytest.mark.parametrize(("variable", "value", "refusal"), [
    ("ANTHROPIC_AUTH_TOKEN", "made-up-token", "unset ANTHROPIC_AUTH_TOKEN"),
    ("ANTHROPIC_BASE_URL", "https://proxy.example.com", "unset ANTHROPIC_BASE_URL"),
    ("ANTHROPIC_BASE_URL", "http://api.anthropic.com", "unset ANTHROPIC_BASE_URL"),
])
def test_a_variable_that_would_send_the_key_elsewhere_is_refused_before_any_export(key_file, variable, value,
                                                                                    refusal):
    write(key_file, KEY)
    os.environ[variable] = value
    before = dict(os.environ)
    with pytest.raises(ValueError, match=refusal):
        models.prepare_env(OPUS)
    assert os.environ == before and os.environ["ANTHROPIC_API_KEY"] == OLDER
    with pytest.raises(run.Refusal, match=refusal):
        run.require_judge_key("claude-haiku-5-5", 2)
    assert os.environ == before


@pytest.mark.parametrize("value", [None, "https://api.anthropic.com", "https://api.anthropic.com/"])
def test_anthropics_own_host_or_no_base_url_is_fine(key_file, value):
    write(key_file, KEY)
    if value is not None:
        os.environ["ANTHROPIC_BASE_URL"] = value
    models.prepare_env(OPUS)
    assert os.environ["ANTHROPIC_API_KEY"] == KEY
    assert models.providers()["anthropic"]["pinned_base_url"] == "https://api.anthropic.com"
    assert models.providers()["anthropic"]["shadow_env"] == ["ANTHROPIC_AUTH_TOKEN"]


def test_a_run_refuses_a_bad_key_file_and_the_judge_takes_its_key_from_the_file(key_file):
    write(key_file, "sk-ant-secretpart\n", 0o644)
    with pytest.raises(run.Refusal, match="chmod 600"):
        run.require_model(OPUS)
    with pytest.raises(run.Refusal, match="chmod 600"):
        run.require_judge_key("claude-haiku-5-5", 2)
    write(key_file, KEY)
    run.require_judge_key("claude-haiku-5-5", 2)
    assert os.environ["ANTHROPIC_API_KEY"] == KEY


def test_every_role_model_takes_the_key_files_key_where_the_swarm_provider_builds_it(key_file):
    write(key_file, KEY)
    model = get_model("swarm/claude-opus-5-5", memoize=False)
    assert model.api.inner.api_key == KEY and os.environ["ANTHROPIC_API_KEY"] == KEY
    offline = get_model("swarm/claude-opus-5-5", memoize=False, api_key="offline")    # a caller's own key stays
    assert offline.api.inner.api_key == "offline"


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


def mixed_run(monkeypatch, tmp_path, proven: bool, model: str = QWEN) -> tuple[int | None, dict, list[str]]:
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
            entry = {**entry, "caching_verified": {"date": "2026-11-25", "inspect": cache.installed_inspect(),
                                                   "route": "anthropic", "entry": models.entry_name(name)}}
        return entry

    monkeypatch.setattr(models, "prepare_env", lambda model: prepared.append(model) or prepare_env(model))
    monkeypatch.setattr(models, "entry_of", recorded)
    monkeypatch.setattr(run.inspect_ai, "eval", eval_stub)
    monkeypatch.setattr(run.settings, "LOGS", tmp_path)
    monkeypatch.setattr(run, "access_line", lambda task: "== access")
    config = build_config("default", "small", ["judge=none", *ROLES])
    try:
        code = run.run("x", ProteinDesign(), config, models.resolve(model), None, task=object())
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
                                                                                       key_file, capsys):
    write(key_file, KEY)
    code, seen, _ = mixed_run(monkeypatch, tmp_path, proven=True)
    assert code is None and seen["cost_limit"] == 5.0
    [line] = [x for x in capsys.readouterr().out.splitlines() if x.startswith("== models")]
    qwen, sonnet = f"{models.resolve(QWEN)} [key OPENROUTER_API_KEY]", f"anthropic/claude-sonnet-5-5 [key {key_file}]"
    assert line == f"== models run={qwen} worker={qwen} regulator={OPUS} [key {key_file}] disregulator={sonnet}"
    assert KEY not in line
    for name in (QWEN, "claude-opus-5-5", "claude-sonnet-5-5"):
        for lookup in cost_cap.lookup_names(models.resolve(name)):
            assert _get_model_info_direct(get_model(lookup, memoize=False, api_key="made-up")).cost \
                == cost_cap.model_cost(name), lookup


# ---------------------------------------------------------------------------------------------- 6. inspect eval
def built_under_inspect_eval(eval_model: str, overrides: dict, roles: dict | None = None):
    """tasks.benchmark_task as `inspect eval src/tasks.py@protein_design` builds it: the eval's model and the
    command line's model roles active, in a context of its own so nothing leaks into other tests."""
    def build():
        init_active_model(get_model(eval_model, memoize=False, api_key="made-up"), GenerateConfig())
        init_model_roles(roles or {})
        return tasks.benchmark_task("protein_design", "default", None, dict(overrides))
    return contextvars.copy_context().run(build)


@pytest.mark.parametrize(("eval_model", "overrides", "roles", "paid"), [
    ("mockllm/model", {}, None, "openai/gpt-6-luna"),                                     # the default judge
    (HAIKU, {"judge": "none"}, None, HAIKU),
    ("swarm/claude-haiku-5-5", {"judge": "none"}, None, "swarm/anthropic/claude-haiku-5-5"),
    ("mockllm/model", {"judge": "none", "regulators": "1", "regulator_model": "claude-opus-5-5"}, None, OPUS),
    ("mockllm/model", {"judge": "none"}, {"judge": "openai/gpt-6-luna"}, "openai/gpt-6-luna"),
])
def test_inspect_eval_of_a_task_refuses_every_paid_model_and_names_the_way_to_run_one(key_file, eval_model, overrides,
                                                                                       roles, paid):
    write(key_file, KEY)
    bound = {role: get_model(name, memoize=False, api_key="made-up") for role, name in (roles or {}).items()}
    with pytest.raises(ValueError, match="python -m src run") as refusal:
        built_under_inspect_eval(eval_model, overrides, bound)
    assert f"{paid} is paid" in str(refusal.value) and "run.run" in str(refusal.value)


def test_inspect_eval_of_a_task_on_mock_models_alone_builds_it():
    task = built_under_inspect_eval("mockllm/model", {"judge": "none", "regulators": "1",
                                                      "regulator_model": "mockllm/model"})
    assert task.dataset[0].metadata["cheatbench"]["role_models"]["regulator"] == "mockllm/model"


# ---------------------------------------------------------------------------------------------- 7. the workspace
WORKSPACE, HEADER = "wrkspc_01AbCdEf23", "anthropic-workspace-id"


def workspace_file(key_file: Path, text: str = WORKSPACE + "\n", mode: int = 0o600) -> Path:
    return write(key_file.with_name(key_file.name + ".workspace"), text, mode)


def sent(name: str, **model_args) -> httpx2.Request:
    """The one request a model get_model builds sends, answered by a fake Anthropic: no network."""
    requests: list = []

    def answer(request):
        requests.append(request)
        return httpx2.Response(200, json=check.FAKE_COMPLETION["anthropic"])

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(answer))
    model = get_model(name, memoize=False, config=GenerateConfig(max_tokens=16), http_client=client, **model_args)
    asyncio.run(model.generate("Hello."))         # a short answer is not streamed
    [request] = requests
    return request


def test_the_workspace_file_sits_beside_the_key_file_and_names_anthropics_header(key_file):
    assert models.workspace_path("anthropic") == key_file.with_name("anthropic_key.workspace")
    assert models.providers()["anthropic"]["workspace_header"] == HEADER
    assert all(models.workspace_path(name) is None for name in models.providers() if name != "anthropic")


def test_with_a_workspace_file_every_request_carries_the_header_and_without_it_none(key_file):
    write(key_file, KEY)
    role = sent("swarm/claude-haiku-5-5")                   # a role model: the key and the header from the files
    assert HEADER not in role.headers and role.headers["authorization"] == f"Bearer {KEY}"
    assert HEADER not in check.offline_request("claude-haiku-5-5", [ChatMessageUser(content="Hi")]).headers
    assert models.model_args("claude-haiku-5-5") == {}
    workspace_file(key_file)
    role = sent("swarm/claude-haiku-5-5")
    assert role.headers[HEADER] == WORKSPACE and role.headers["authorization"] == f"Bearer {KEY}"
    assert check.offline_request("claude-haiku-5-5", [ChatMessageUser(content="Hi")]).headers[HEADER] == WORKSPACE
    assert sent(HAIKU, **models.model_args(HAIKU)).headers[HEADER] == WORKSPACE
    assert models.model_args(HAIKU) == {"default_headers": {HEADER: WORKSPACE}}
    assert HEADER not in str(models.model_args("claude-haiku-5-5-openrouter"))     # OpenRouter's route keeps its own


def test_the_runs_model_and_the_judges_carry_the_header(monkeypatch, tmp_path, key_file):
    write(key_file, KEY)
    workspace_file(key_file)
    code, seen, _ = mixed_run(monkeypatch, tmp_path, proven=True, model=HAIKU)
    assert code is None and seen["model"].api.client.default_headers[HEADER] == WORKSPACE
    config = build_config("default", "small", ["judge=claude-haiku-5-5"])
    assert run.judge_role_model(config).api.client.default_headers[HEADER] == WORKSPACE
    judge = judging.Judge.of("claude-haiku-5-5", batch=False)
    assert judge.model.api.client.default_headers[HEADER] == WORKSPACE


def test_the_paid_checks_plan_names_the_workspace_header_without_its_value(monkeypatch, capsys, key_file):
    write(key_file, KEY)
    workspace_file(key_file)
    assert check.main(["--model", "claude-haiku-5-5"]) == 2
    out = capsys.readouterr().out
    assert f"the header {HEADER} from {key_file}.workspace" in out and WORKSPACE not in out


@pytest.mark.parametrize(("text", "mode"), [
    ("wrkspc_01AbCdEf23\n", 0o644), ("wrkspc_01AbCd-Ef23\n", 0o600), ("workspace_01\n", 0o600),
    ("wrkspc_\n", 0o600), ("wrkspc_01AbCdEf23\nwrkspc_2\n", 0o600),
])
def test_a_bad_workspace_file_is_refused_before_any_export_naming_only_its_path(key_file, text, mode):
    write(key_file, KEY)
    path = workspace_file(key_file, text, mode)
    before = dict(os.environ)
    with pytest.raises(ValueError, match=re.escape(str(path))) as refusal:
        models.prepare_env(HAIKU)
    assert "01AbCd" not in str(refusal.value) and os.environ == before
    with pytest.raises(run.Refusal, match=re.escape(str(path))):
        run.require_model(HAIKU)
