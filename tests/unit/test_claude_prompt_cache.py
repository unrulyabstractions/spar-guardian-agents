"""Claude's cache marks on both routes, read from the bytes Inspect's real request code sends through the swarm provider
to a fake OpenRouter or a fake Anthropic: never more than Anthropic's four, on the system prompt, the last tool and the
last two blocks of the history (at Anthropic the last through its top-level automatic mark), none for other families;
Claude refused on either route until a paid check under the installed Inspect is recorded; and the paid check sends
nothing without --yes."""
from __future__ import annotations

import datetime as dt
import os

import pytest
from inspect_ai.model import ChatMessageAssistant, ChatMessageSystem, ChatMessageTool, ChatMessageUser
from inspect_ai.tool import ToolCall

from scripts import check_anthropic_cache as check
from scripts.check_anthropic_cache import TOOLS, request_body
from src.models import claude_prompt_cache as cache
from src.models import model_registry as models

CLAUDE = sorted(name for name in models.entries() if cache.claude_route(name) == "openrouter")
DIRECT = sorted(name for name in models.entries() if cache.claude_route(name) == "anthropic")
OTHERS = ["gpt-6-luna-openrouter", "deepseek-flash-openrouter", "gemini-4-argon-openrouter"]


def tool_turn(*ids: str, text: str = "") -> list:
    """An assistant's tool calls (in parallel when several) and their results."""
    calls = [ToolCall(id=i, function="board_read", arguments={}) for i in ids]
    return [ChatMessageAssistant(content=text, tool_calls=calls),
            *(ChatMessageTool(content=f"posts for {i}", tool_call_id=i, function="board_read") for i in ids)]


START = [ChatMessageSystem(content="You oversee a team."), ChatMessageUser(content="Begin.")]
HISTORIES = {
    "a tool result": [*START, *tool_turn("c1")],
    "the round line": [*START, *tool_turn("c1"), ChatMessageUser(content="[Round 2 of 10] The board changed.")],
    "parallel tool results": [*START, *tool_turn("c1"), *tool_turn("c2", "c3", "c4")],
    "a long run": [*START, *(m for k in range(12) for m in tool_turn(f"c{k}", text=f"Step {k}.")),
                   ChatMessageUser(content="[Round 3 of 10]"), ChatMessageSystem(content="A second system note."),
                   *tool_turn("c99")],
    "an assistant reply": [*START, *tool_turn("c1"), ChatMessageAssistant(content="Done.")],
}


@pytest.fixture(autouse=True)
def dummy_key(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "dummy")


@pytest.mark.parametrize("ending", HISTORIES)
@pytest.mark.parametrize("name", CLAUDE + DIRECT)
def test_a_claude_request_never_carries_more_than_four_cache_marks(name, ending):
    """Anthropic refuses a fifth mark with HTTP 400 (2026-11-22), so a fifth would end every Claude run."""
    marks = cache.breakpoints(request_body(name, HISTORIES[ending]))
    assert len(marks) <= cache.MAX_BREAKPOINTS, marks


@pytest.mark.parametrize("name", CLAUDE)
def test_the_marks_sit_on_the_system_prompt_the_last_tool_and_the_last_two_history_blocks(name):
    """The mark on the last block writes each call's whole prompt; the one before is the lookback's fallback. Nothing
    goes top-level, which would make a fifth."""
    body = request_body(name, HISTORIES["the round line"])   # system, user, assistant (no text), tool, user
    assert cache.breakpoints(body) == [f"tool {TOOLS[-1].name}", "system 0", "tool 3", "user 4"]
    assert body["messages"][3]["content"] == [{"type": "text", "text": "posts for c1",
                                               "cache_control": {"type": "ephemeral"}}]
    assert body["provider"] == models.routing(models.resolve(name))


@pytest.mark.parametrize("ending", HISTORIES)
def test_a_claude_request_marks_its_last_text_block(ending):
    body = request_body("claude-haiku-5-5-openrouter", HISTORIES[ending])
    last = len(body["messages"]) - 1
    assert f"{body['messages'][last]['role']} {last}" in cache.breakpoints(body)
    assert cache.shape_problem(body, "openrouter") is None


@pytest.mark.parametrize("name", DIRECT)
def test_at_anthropic_the_marks_are_the_automatic_one_the_system_prompt_the_last_tool_and_the_lookback(name):
    """Inspect's Anthropic provider sends Anthropic's automatic caching (top-level, onto the last cacheable block) and
    marks the block before the last one, so the last message is covered without a mark of its own."""
    body = request_body(name, HISTORIES["a tool result"])      # user, assistant (tool use), user (tool result)
    assert body["model"] == models.resolve(name).removeprefix("anthropic/")
    assert cache.breakpoints(body) == ["top-level", "system", f"tool {TOOLS[-1].name}", "assistant 1"]
    assert cache.shape_problem(body, "anthropic") is None


@pytest.mark.parametrize("ending", HISTORIES)
def test_at_anthropic_every_history_passes_the_offline_shape_check(ending):
    assert cache.shape_problem(request_body("claude-haiku-5-5", HISTORIES[ending]), "anthropic") is None


def test_the_shape_check_refuses_a_fifth_mark_and_an_uncovered_last_message():
    marked = {"type": "text", "text": "x", "cache_control": {"type": "ephemeral"}}
    body = {"cache_control": {}, "system": [marked], "tools": [{"name": "t", "cache_control": {}}],
            "messages": [{"role": "user", "content": [marked, marked]}]}
    assert "5 cache marks" in cache.shape_problem(body, "anthropic")
    plain = {"messages": [{"role": "user", "content": [marked]}, {"role": "user", "content": "y"}]}
    assert "no cache mark" in cache.shape_problem(plain, "anthropic")
    assert "no cache mark" in cache.shape_problem({**plain, "cache_control": {}}, "openrouter")
    assert cache.shape_problem({**plain, "cache_control": {}}, "anthropic") is None


@pytest.mark.parametrize("name", OTHERS)
def test_other_families_get_no_marks_and_their_strings_stay_strings(name):
    body = request_body(name, HISTORIES["parallel tool results"])
    assert cache.breakpoints(body) == []
    assert all(isinstance(m["content"], str) for m in body["messages"] if m["role"] in ("user", "tool", "system"))


def test_each_route_of_a_model_is_its_own_route():
    assert cache.claude_route("claude-opus-5-5-openrouter") == "openrouter"
    assert cache.claude_route("swarm/claude-opus-5-5") == "anthropic"
    assert cache.claude_route("anthropic/claude-haiku-5-5") == "anthropic"
    assert cache.claude_route("gpt-6-luna-openrouter") is None and cache.claude_route("mockllm/model") is None


@pytest.mark.parametrize("entry", ["claude-opus-5-5-openrouter", "claude-opus-5-5"])
def test_claude_is_refused_on_either_route_until_a_check_under_the_installed_inspect_is_recorded(monkeypatch, entry):
    opus = f"swarm/{entry}"
    unproven = {k: v for k, v in models.entry_of(entry).items() if k != "caching_verified"}

    def recorded(value):
        entry = dict(unproven, caching_verified=value)
        monkeypatch.setattr(cache.models, "entry_of", lambda name: entry if "claude" in name else unproven)

    monkeypatch.setattr(cache.models, "entry_of", lambda name: unproven)
    with pytest.raises(ValueError, match="refused until scripts/check_anthropic_cache.py"):
        cache.require_proven([models.resolve("gpt-6-luna-openrouter"), opus])
    cache.require_proven([models.resolve("gpt-6-luna-openrouter"), "mockllm/model", None])

    recorded("2026-11-02")   # the older form, from before the Inspect commit was recorded
    with pytest.raises(ValueError, match="refused until"):
        cache.require_proven([opus])
    own = {"route": cache.claude_route(entry), "entry": entry}
    recorded({"date": "2026-11-02", "inspect": "c060c201c", **own})
    with pytest.raises(ValueError, match=f"names Inspect c060c201c, and Inspect {cache.installed_inspect()} is"):
        cache.require_proven([opus])
    recorded({"date": "2026-11-22", "inspect": cache.installed_inspect(), **own})
    cache.require_proven([opus])


@pytest.mark.parametrize("entry", ["claude-opus-5-5-openrouter", "claude-opus-5-5"])
def test_a_stamp_proves_only_its_own_route_and_entry(monkeypatch, entry):
    other_route = {"openrouter": "anthropic", "anthropic": "openrouter"}[cache.claude_route(entry)]
    other_entry = entry.replace("opus", "haiku")
    unproven = {k: v for k, v in models.entry_of(entry).items() if k != "caching_verified"}
    inspect = {"date": "2026-11-25", "inspect": cache.installed_inspect()}
    for stamp in ({**inspect, "route": other_route, "entry": entry},
                  {**inspect, "route": cache.claude_route(entry), "entry": other_entry},
                  inspect):           # the form of before 2026-11-25, with neither
        monkeypatch.setattr(cache.models, "entry_of", lambda name, stamp=stamp: dict(unproven, caching_verified=stamp))
        with pytest.raises(ValueError, match="passes for this entry: caching_verified names route"):
            cache.require_proven([f"swarm/{entry}"])
    monkeypatch.setattr(cache.models, "entry_of", lambda name: dict(
        unproven, caching_verified={**inspect, "route": cache.claude_route(entry), "entry": entry}))
    cache.require_proven([f"swarm/{entry}", models.resolve(entry)])


def test_every_recorded_check_names_its_date_inspect_route_and_entry_and_an_unchecked_entry_is_refused():
    stamped = {name for name in CLAUDE + DIRECT if models.entries()[name].get("caching_verified") is not None}
    assert set(CLAUDE) <= stamped
    for name in stamped:
        verified = models.entries()[name]["caching_verified"]
        assert set(verified) == {"date", "inspect", "route", "entry"}, name
        assert (verified["route"], verified["entry"]) == (cache.claude_route(name), name)
        assert cache.same_commit(str(verified["inspect"]), cache.installed_inspect()), name
    cache.require_proven(sorted(stamped))          # every recorded check passes for its own route and entry
    unchecked = sorted(set(DIRECT) - stamped)
    assert unchecked                               # the older direct entries were never checked
    for name in unchecked:
        with pytest.raises(ValueError, match="Claude at Anthropic is refused until"):
            cache.require_proven([name])


def test_the_stamp_line_names_the_models_route_and_entry():
    assert cache.stamp("claude-haiku-5-5", "2026-11-25") == (
        f'caching_verified: {{date: "2026-11-25", inspect: "{cache.installed_inspect()}", route: anthropic, '
        "entry: claude-haiku-5-5}")
    assert cache.stamp("anthropic/claude-haiku-5-5", "2026-11-25") == cache.stamp("claude-haiku-5-5", "2026-11-25")
    assert "route: openrouter, entry: claude-opus-5-5-openrouter}" in cache.stamp("claude-opus-5-5-openrouter", "d")


def test_one_commit_abbreviated_to_two_lengths_is_the_same_inspect():
    assert cache.same_commit("c574b6bf6", "c574b6bf6674e6d5") and cache.same_commit("c574b6bf6674", "c574b6bf6")
    assert not cache.same_commit("c574b6bf6", "c060c201c") and not cache.same_commit("0.3.292", "0.3.29")


@pytest.fixture
def key_file(monkeypatch, tmp_path):
    """A made-up Anthropic key in a key file of its own, and a private copy of the environment."""
    monkeypatch.setattr(os, "environ", dict(os.environ))
    path = tmp_path / "anthropic_key"
    path.write_text("sk-ant-made-up-key\n")
    path.chmod(0o600)
    monkeypatch.setattr(models, "key_path", lambda provider: path if provider == "anthropic" else None)
    return path


def fake_usages(read_shares: list[float]) -> list[dict]:
    """Three calls' usage, each later one reading this share of the previous call's prompt from the cache."""
    prompts = [2400, 2450, 2520]
    usages = [{"input_tokens": 3, "input_tokens_cache_write": prompts[0] - 3, "input_tokens_cache_read": 0,
               "output_tokens": 16}]
    for previous, prompt, share in zip(prompts, prompts[1:], read_shares):
        read = int(previous * share)
        usages.append({"input_tokens": 3, "input_tokens_cache_write": prompt - read - 3,
                       "input_tokens_cache_read": read, "output_tokens": 16})
    return usages


def test_the_check_of_a_direct_entry_sends_nothing_without_yes(monkeypatch, capsys, key_file):
    async def no_call(name, calls):
        raise AssertionError("a call was sent")

    monkeypatch.setattr(check, "paid_calls", no_call)
    os.environ["ANTHROPIC_API_KEY"] = "sk-ant-the-environments-older-key"
    assert check.main(["--model", "claude-opus-5-5"]) == 2
    out = capsys.readouterr().out
    assert out.count("marks: ['top-level', 'system', 'tool bash',") == 3
    assert "nothing sent" in out and f"with the key from {key_file}; no workspace header" in out
    # without --yes the key file is not even read
    assert "sk-ant-made-up-key" not in out and os.environ["ANTHROPIC_API_KEY"] == "sk-ant-the-environments-older-key"


def test_the_check_of_a_direct_entry_passes_and_prints_its_caching_verified_line(monkeypatch, capsys, key_file):
    async def calls(name, histories):
        assert name == "claude-haiku-5-5" and len(histories) == 3
        return fake_usages([0.99, 0.99])

    monkeypatch.setattr(check, "paid_calls", calls)
    assert check.main(["--model", "claude-haiku-5-5", "--yes"]) == 0
    out = capsys.readouterr().out
    assert "PASS" in out and cache.stamp("claude-haiku-5-5", dt.date.today().isoformat()) in out.splitlines()
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-made-up-key"


def test_the_check_of_a_direct_entry_fails_when_a_later_call_reads_too_little(monkeypatch, capsys, key_file):
    async def calls(name, histories):
        return fake_usages([0.99, 0.5])

    monkeypatch.setattr(check, "paid_calls", calls)
    assert check.main(["--model", "claude-haiku-5-5", "--yes"]) == 1
    assert "caching_verified" not in capsys.readouterr().out


def test_the_check_of_a_direct_entry_refuses_without_its_key_file(monkeypatch, capsys, key_file):
    async def no_call(name, calls):
        raise AssertionError("a call was sent")

    monkeypatch.setattr(check, "paid_calls", no_call)
    key_file.unlink()
    os.environ["ANTHROPIC_API_KEY"] = "sk-ant-the-environments-older-key"
    assert check.main(["--model", "claude-haiku-5-5", "--yes"]) == 2
    assert f"write the key to {key_file} (chmod 600)" in capsys.readouterr().out
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-the-environments-older-key"


def test_the_check_refuses_a_model_that_is_not_claude(capsys):
    assert check.main(["--model", "gpt-6-luna-openrouter", "--yes"]) == 2
