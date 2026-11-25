"""The belief questions asked from a study's setup (scripts/study/belief_questions.py): which teams are asked, what a
team's calls may cost, and the table. No model call, no Docker."""
from __future__ import annotations

from inspect_ai.model import ChatMessageSystem, ChatMessageUser

from scripts.study import belief_questions as bq

PLAN = {"study": "s", "seeds": [1], "settings": [], "targets": {
    "opus_team": {"module": "studies.cells.swe.ian_gflipt_minimal", "model": "claude-opus-5-5", "packs": ["none"],
                  "settings": ["n_agents=4", "disregulators=1"]},
    "opus_alone": {"module": "studies.cells.swe.ian_gflipt_minimal", "model": "claude-opus-5-5", "packs": ["none"],
                   "seeds": [1, 2], "settings": ["n_agents=3"]},
    "haiku_openrouter": {"module": "studies.cells.swe.ian_gflipt_minimal", "model": "claude-haiku-5-5-openrouter",
                         "packs": ["none"], "settings": ["n_agents=3"]},
    "haiku_direct": {"module": "studies.cells.swe.ian_gflipt_minimal", "model": "claude-haiku-5-5",
                     "packs": ["none"], "settings": ["n_agents=5"]}}}


def test_each_model_is_asked_once_on_its_smallest_team_weakest_first():
    """A model reached by two routes is one model; its smallest team is asked, on the target's first seed."""
    cells = bq.chosen(PLAN, [])
    assert [(cell.target, cell.seed) for cell in cells] == [("haiku_openrouter", 1), ("opus_alone", 1)]
    assert [cell.target for cell in bq.chosen(PLAN, ["opus_team"])] == ["opus_team"]


def test_a_team_is_priced_before_it_is_asked():
    """Every prompt uncached at four characters a token, and MAX_REPLY_TOKENS of output a call."""
    workers = {"a": [ChatMessageSystem(content="s" * 4000), ChatMessageUser(content="u" * 4000)]}
    asked = {"q1": "one?", "q2": "two?"}
    prices = bq.models.usd_per_million("claude-opus-5-5")
    most = bq.at_most("claude-opus-5-5", workers, asked)
    assert most == ((2000 * 2 + 2 * 200) * prices["input"] + 2 * bq.MAX_REPLY_TOKENS * prices["output"]) / 1e6


def test_the_table_gives_each_model_its_mean_and_its_answers():
    rows = [{"model": "claude-opus-5.5", "answers": {"a": {"q": {"answer": 80}}, "b": {"q": {"answer": 90}},
                                                     "c": {"q": {"answer": None}}}}]
    text = bq.table(rows, ["q"])
    assert "Claude Opus 5.5" in text and "85 [80, 90]" in text
