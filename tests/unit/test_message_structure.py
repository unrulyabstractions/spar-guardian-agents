"""The shape of what an agent reads: each round's one message, each notice inside a round as the last paragraph of the
tool result the agent receives next, a team's hand-in told once, a peer that came and went told by its exit alone, a
round's message that ORBIT rejects losing nothing, and the run's record of every notice an agent got. Mock model, no
Docker, no paid call."""
from __future__ import annotations

import json

from inspect_ai.model import ChatMessageTool, ChatMessageUser, ContentText
from inspect_ai.tool import ToolCallError

from src.export.atif_transcripts import build_trajectory
from src.export.eval_sample_view import SampleView, agent_messages
from src.swarm import turn_rules as rules
from src.swarm.turn_rules import _turn_rules_filter as rules_filter
from src.swarm.turn_rules._turn_rules_filter import TurnRulesDefense, with_paragraph
from src.swarm.turn_rules._turn_state import LIVE, Live
from tests.support.mock_model import tagged_infos
from tests.support.turn_rules_harness import (
    PROMPTS,
    board_post,
    by_agent,
    do,
    pull,
    run,
    sequential,
    stop,
    store,
    submit,
)

FULL = PROMPTS["notice"]["full"]


def test_a_notice_inside_a_round_is_the_last_paragraph_of_the_next_tool_result(tmp_path):
    """a02 posts at its first call of a concurrent round of two calls: a01's second call gets the notice at the end of
    its first call's tool result, with no bracketed label and no message of its own, and every later call keeps it
    there. The durable conversation never holds it; the transcript shows the result as the provider received it."""
    scripts = {"a01": [do("A1"), do("A2"), do("A3")], "a02": [board_post("I take part one"), do("B")],
               "a03": [do("C1"), do("C2"), do("C3")]}
    sample, rec, log = run(pull(rounds=2, calls_per_round=2), scripts, tmp_path, with_log=True)
    notice = f"Posts on the board: 1 by a02.\n- a02: I take part one\n{FULL}"
    result = f"a01 worked on A1\n\n{notice}"
    calls = by_agent(rec, "a01")
    assert result not in calls[0]["texts"]
    for c in calls[1:]:
        k = c["texts"].index(result)
        assert c["roles"][k] == "tool" and c["roles"][k + 1:k + 2] in ([], ["assistant"])
        assert not c["added"] and not any("[" in t.split("\n")[0] for t in c["texts"] if t.startswith("Posts"))
    # the conversation is the agent's own: the tool result there is the tool's, unchanged
    view = SampleView.from_eval_sample(sample, log)
    held = [m for m in agent_messages(view, "a01") if isinstance(m, ChatMessageTool)]
    assert held[0].text == "a01 worked on A1"
    [info] = [i for i in tagged_infos(sample, rules.TURN_RULES_TAG) if i["kind"] == "notice" and i["agent"] == "a01"]
    assert info["into"] == held[0].id and "after" not in info and info["content"] == notice
    # the transcript gives what the provider received, and lists the paragraph it added
    steps = json.loads(build_trajectory(view, "a01")[0].model_dump_json())["steps"]
    results = [r for s in steps for r in ((s.get("observation") or {}).get("results") or [])]
    assert results[0]["content"] == result
    assert results[0]["extra"] == {rules.NOTICE_PARAGRAPH_TAG: [notice]}
    assert not [s for s in steps if s["source"] == "user" and "Posts on the board" in s["message"]]


def test_a_notice_keeps_a_tool_result_a_tool_result():
    """The provider receives an error's message in place of a failed call's content, so the notice goes there; content
    lists keep their items. The copy keeps the role, the id and the tool call id."""
    failed = ChatMessageTool(content="", tool_call_id="c1", function="work",
                             error=ToolCallError("unknown", "it failed"))
    copy = with_paragraph(failed, "The board changed.")
    assert (copy.role, copy.id, copy.tool_call_id, copy.function) == ("tool", failed.id, "c1", "work")
    assert copy.error.message == "it failed\n\nThe board changed." and failed.error.message == "it failed"
    listed = ChatMessageTool(content=[ContentText(text="done")], tool_call_id="c2", function="work")
    assert with_paragraph(listed, "The board changed.").text == "done\n\nThe board changed."
    empty = ChatMessageTool(content="", tool_call_id="c3", function="work")
    assert with_paragraph(empty, "The board changed.").content == "The board changed."
    user = ChatMessageUser(content="Tool calls left: up to 4.")
    assert with_paragraph(user, "The board changed.").text == "Tool calls left: up to 4.\n\nThe board changed."


def test_a_team_hand_in_is_told_once(tmp_path):
    """a01 hands in the team's work in round 1. a02's next message tells of it in its presence part and leaves the
    system's post of it out of the board part; no later notice brings that post back."""
    sample, rec = run(sequential(rounds=3, submit_delay_rounds=1),
                      {"a01": [submit()], "a02": [do("B"), do("B2")], "a03": [do("C"), do("C2")]}, tmp_path)
    for agent, peer in (("a02", "a03"), ("a03", "a02")):
        calls = by_agent(rec, agent)
        message = calls[0]["texts"][-1]
        assert message == (f"{rules.round_line(3)}\n\n{peer} entered.\n"
                           f"a01 handed in the team's work (submit). The run ends soon.\na01 exited.")
        assert sum(text.count("handed in") for text in calls[-1]["texts"]) == 1
        assert not any(c["notices"] for c in calls)
    assert store(sample, "notice_seen") == {"a02": 1, "a03": 1}


def test_a_hand_in_with_other_news_on_the_board(tmp_path):
    """A post by a teammate stays in the board part; only the system's post of the hand-in leaves it."""
    sample, rec = run(sequential(rounds=3, submit_delay_rounds=1),
                      {"a01": [board_post("ready"), submit()], "a02": [do("B"), do("B2")], "a03": [do("C")]},
                      tmp_path)
    second = by_agent(rec, "a02")[1]["texts"][-1]
    assert second.count("handed in") == 1 and "Posts on the board" not in second
    assert "- a01: ready" in by_agent(rec, "a02")[0]["texts"][-1]
    assert "system" not in second


def test_a_peer_that_came_and_went_is_told_by_its_exit(tmp_path):
    """a01 stops before a02's first message: a02 reads its exit, never its entry, then or later; the entry is held as
    folded, so no later message brings it."""
    sample, rec = run(sequential(rounds=2), {"a01": [stop()], "a02": [do("B"), do("B2")], "a03": [do("C")]},
                      tmp_path)
    calls = by_agent(rec, "a02")
    assert calls[0]["presence"] == ["a03 entered.\na01 stopped working (stop_working).\na01 exited."]
    assert not any("a01 entered" in t for c in calls for t in c["texts"])
    assert "entered:a01" in calls[-1]["events"]
    delivered = [i for i in tagged_infos(sample, rules.PRESENCE_TAG)
                 if i.get("event") == "delivered" and i["to"] == "a02" and i["id"] == "entered:a01"]
    assert len(delivered) == 1 and delivered[0]["folded"] and delivered[0]["content"] == ""


def test_a_rejected_round_message_loses_no_board_part(tmp_path, monkeypatch):
    """ORBIT may reject a boundary edit (a message over the member's limits): the proposal is dropped and the
    conversation keeps what it held. a03's first round's message is proposed on a copy that is dropped, as ORBIT drops
    a rejected one: the post it told of is not marked seen, so a03's first call still gets it, as the last paragraph
    of its last message, and nothing of the dropped message is recorded."""
    original = TurnRulesDefense.apply_pre_turn
    dropped: list[str] = []

    async def rejecting(self, state, turn, agent_states=None):
        [(name, conversation)] = agent_states.items()
        if name == "a03" and not dropped:
            proposal = type(conversation)(messages=list(conversation.messages))
            await original(self, state, turn, agent_states={name: proposal})
            dropped.append(proposal.messages[-1].text)
            return None
        return await original(self, state, turn, agent_states=agent_states)

    monkeypatch.setattr(TurnRulesDefense, "apply_pre_turn", rejecting)
    sample, rec = run(sequential(rounds=2, notice="counts"),
                      {"a01": [do("A")], "a02": [board_post("x"), do("B")], "a03": [do("C"), do("C2")]}, tmp_path)
    assert dropped and dropped[0].endswith("Posts on the board: 1 by a02.")
    first, second = by_agent(rec, "a03")
    assert first["texts"] == ["Fix the issue together.\n\nPosts on the board: 1 by a02."]
    assert "Posts on the board" not in second["texts"][-1]
    notices = [i for i in tagged_infos(sample, rules.TURN_RULES_TAG) if i["kind"] == "notice" and i["agent"] == "a03"]
    assert [(i["round"], i.get("boundary"), bool(i.get("into"))) for i in notices] == [(1, None, True)]
    rounds = [i["round"] for i in tagged_infos(sample, rules.ROUND_TAG) if i["agent"] == "a03"]
    assert rounds == [2] and store(sample, "notice_seen")["a03"] == 1


def test_the_run_records_every_notice_an_agent_got(tmp_path):
    """TurnState.added, which run.json's rules.added holds, keeps the board part of each round's message (boundary, `in`
    the message that holds it) beside each notice inside a round (`into` the tool result it ended:
    test_a_notice_inside_a_round_is_the_last_paragraph_of_the_next_tool_result); the rounds' history marks fall
    between the rounds' messages, so the peer tools give each call its round."""
    scripts = {"a01": [do("A1"), do("A2"), do("A3"), do("A4")],
               "a02": [board_post("one"), do("B"), board_post("two"), do("B2")],
               "a03": [do("C1"), do("C2"), do("C3"), do("C4")]}
    sample, rec, log = run(sequential(rounds=2, calls_per_round=2), scripts, tmp_path, with_log=True)
    view = SampleView.from_eval_sample(sample, log)
    for agent, told in (("a01", ["Posts on the board: 1 by a02."]),
                        ("a03", ["Posts on the board: 1 by a02.", "The board changed: a02 1 new of 2."])):
        added = store(sample, "added")[agent]
        messages = {m.id: m for m in agent_messages(view, agent)}
        assert [e["text"].splitlines()[0] for e in added] == told and all(e["boundary"] for e in added)
        assert all(rules.NOTICE_TAG in (messages[e["in"]].metadata or {}) for e in added)
        assert all(messages[e["in"]].text.endswith(e["text"]) for e in added)
        infos = [i for i in tagged_infos(sample, rules.TURN_RULES_TAG) if i["kind"] == "notice" and i["agent"] == agent]
        assert len(infos) == len(added)
        marks = store(sample, "history_marks")[agent]
        rounds = 0
        for index, message in enumerate(messages.values()):
            tag = rules.message_round(message.metadata if isinstance(message, ChatMessageUser) else None)
            if tag:
                rounds += 1
                assert next(r for a, r in marks if index < a) == tag["round"], (agent, index, marks)
        assert rounds == len(marks) == 2


def test_settle_records_once_and_only_what_the_conversation_holds():
    """settle runs each pending record of a message the conversation holds, once, given its id."""
    held, other = ChatMessageUser(content="held"), ChatMessageUser(content="other")
    seen: list[str] = []
    token = LIVE.set(Live(pending={held.id: [seen.append], other.id: [seen.append]}))
    try:
        rules_filter.settle([held])
        rules_filter.settle([held])
        assert seen == [held.id] and list(LIVE.get().pending) == [other.id]
    finally:
        LIVE.reset(token)


def test_a_notice_follows_a_result_that_ends_with_a_newline_after_one_blank_line():
    """A shell's output ends with a newline: the notice still stands one blank line below its last line, in the
    provider's copy and in the transcript."""
    from src.export.atif_transcripts import with_paragraphs

    result = ChatMessageTool(content="waited\n", tool_call_id="c1", function="shell")
    assert with_paragraph(result, "The board changed.").text == "waited\n\nThe board changed."
    assert with_paragraphs("waited\n", ["The board changed."]) == "waited\n\nThe board changed."
    assert with_paragraphs("", ["a", "b"]) == "a\n\nb"
