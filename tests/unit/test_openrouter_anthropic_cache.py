"""Claude's cache marks through OpenRouter, read from the bytes Inspect's real request code sends through the swarm
provider to a fake OpenRouter: never more than Anthropic's four, on the system prompt, the last tool and the last two
text blocks of the history, none for other families; and Claude refused until a paid check under the installed Inspect
is recorded."""
from __future__ import annotations

import pytest
from inspect_ai.model import ChatMessageAssistant, ChatMessageSystem, ChatMessageTool, ChatMessageUser
from inspect_ai.tool import ToolCall

from scripts.check_anthropic_cache import TOOLS, request_body
from src.models import model_registry as models
from src.models import openrouter_anthropic_cache as cache

CLAUDE = sorted(name for name in models.entries() if cache.is_claude_on_openrouter(name))
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
@pytest.mark.parametrize("name", CLAUDE)
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


@pytest.mark.parametrize("name", OTHERS)
def test_other_families_get_no_marks_and_their_strings_stay_strings(name):
    body = request_body(name, HISTORIES["parallel tool results"])
    assert cache.breakpoints(body) == []
    assert all(isinstance(m["content"], str) for m in body["messages"] if m["role"] in ("user", "tool", "system"))


def test_claude_through_openrouter_is_refused_until_a_check_under_the_installed_inspect_is_recorded(monkeypatch):
    opus = "swarm/claude-opus-5-5-openrouter"
    with pytest.raises(ValueError, match="refused until scripts/check_anthropic_cache.py"):
        cache.require_proven([models.resolve("gpt-6-luna-openrouter"), opus])
    cache.require_proven([models.resolve("gpt-6-luna-openrouter"), "mockllm/model", None])

    def recorded(value):
        entry = dict(models.entry_of("claude-opus-5-5-openrouter"), caching_verified=value)
        monkeypatch.setattr(cache.models, "entry_of", lambda name: entry)

    recorded("2026-11-02")   # the older form, from before the Inspect commit was recorded
    with pytest.raises(ValueError, match="refused until"):
        cache.require_proven([opus])
    recorded({"date": "2026-11-02", "inspect": "c060c201c"})
    with pytest.raises(ValueError, match=f"names Inspect c060c201c, and Inspect {cache.installed_inspect()} is"):
        cache.require_proven([opus])
    recorded({"date": "2026-11-22", "inspect": cache.installed_inspect()})
    cache.require_proven([opus])


def test_every_recorded_check_names_its_date_and_inspect_commit():
    for name in CLAUDE:
        verified = models.entries()[name].get("caching_verified")
        if verified is not None:
            assert set(verified) == {"date", "inspect"}, name
