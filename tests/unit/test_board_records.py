"""The board's records of who came and went (the user, 2026-11-22): a post by the system when an agent enters and when
it exits, with no reason, beside the round messages' own lines. board_read shows the records between the posts it
returns; no count and no notice holds them."""
from tests.support.turn_rules_harness import board_post, by_agent, do, read, run, sequential, status, stop, tool_results


def test_the_board_records_each_entry_and_exit_and_no_count_or_notice_holds_them(tmp_path):
    # one call a round, in declaration order: a01 posts and then stops; a02 and a03 work, then read
    scripts = {"a01": [board_post("hello"), stop()], "a02": [do("B1"), read(), status()],
               "a03": [do("C1"), do("C2"), read(latest=1)]}
    sample, rec = run(sequential(rounds=3), scripts, tmp_path)
    reads = [result for function, result in tool_results(sample) if function == "board_read"]
    # the default read: the post, with every record since the post before it, in board order, an exit with no reason
    # a record has no number, so a post's number stays its place among the posts
    assert reads[0].splitlines() == ["[system] a01 entered.", "[a01 #1] hello", "[system] a02 entered.",
                                     "[system] a03 entered.", "[system] a01 exited."]
    # a later read of the newest post alone shows the same records and the exit that came since
    assert reads[1].splitlines()[:5] == reads[0].splitlines()
    assert all(line.startswith("[system] ") and line.endswith(" exited.") for line in reads[1].splitlines()[5:])
    # board_status counts posts alone
    [counted] = [result for function, result in tool_results(sample) if function == "board_status"]
    assert counted.splitlines() == ["a01: 1 posts (0 new)", "total 1, 0 new since your last read"]
    # no notice counts a record, and a round's message still tells who entered and who exited
    told = [text for call in rec for text in call["texts"]]
    assert not any("by system" in text or "system 1 new" in text for text in told), told
    assert any("Posts on the board: 1 by a01." in text for text in by_agent(rec, "a02")[1]["texts"])
    assert any("a01 exited." in text or "a01 stopped working" in text for text in told)


def test_a_lone_agent_has_no_board_record(tmp_path):
    sample, rec = run(sequential(n_agents=1, rounds=2), {"a01": [do("A1"), do("A2")]}, tmp_path)
    assert not any("entered." in result for _, result in tool_results(sample))
