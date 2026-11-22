"""The rounds scenario the openmath and SWE smoke suites share: three workers and a regulator, three rounds each of up
to three tool calls, in sequential rounds in seat order, and the checks of every round's rule on the exported run.

    script, plan = rounds_script(workers, regulator, shell="shell")
    log, summary, records = SUITE.run("rounds", task, scripted(script, recorder))
    check_rounds(SUITE, "rounds", log.samples[0], records, plan, workers, regulator)

With w1, w2, w3 the workers in seat order and the regulator last:

    w1   round 1: board_post, board_read (its own post), shell          ends "calls" (N reached)
         round 2: one reply asking for four shell calls: three run, the fourth is refused, ends "calls"
         round 3: stop_working
    w2   round 1: board_read (w1's post of this round), shell, end_round ends "end_round" after two calls
         round 2: board_status, end_round                                a board round: it uses up no round
         round 3: shell, end_round
         round 4: stop_working, after the line of the regulator that ran out of rounds
    w3   round 1: a reply with no tool call                              ends "no_tool_call", uses up a round
         round 2: shell, end_round
         round 3: stop_working
    reg  rounds 1 to 3: shell (and a board_read in round 1), end_round   then it has run out of rounds
"""
from __future__ import annotations

import json

from inspect_ai.model import ModelOutput

from src.configuration import settings
from src.configuration.prompt_texts import rounds_values
from src.configuration.swarm_config import SwarmConfig
from src.swarm.turn_rules._turn_state import END_ROUND, FREE_TOOLS, PRESENCE_TAG, ROUND_TAG
from tests.support.mock_model import call, calls, text

ROUNDS, CALLS = 3, 3
TEXTS = settings.load_prompts()["round"]
VALUES = settings.load_prompts()["first_values"]
PRESENCE = settings.load_prompts()["presence"]
POST = "round one post: I take the first part"
THINKING = "I will plan before I call a tool."
NOTICE = "[Since your last call]"


def config_pairs() -> list[str]:
    """The fields the scenario runs under, as --set pairs."""
    return ["n_agents=4", "regulators=1", "seed=1", "concurrent_rounds=false", "turn_order=round_robin",
            f"calls_per_round={CALLS}", f"rounds={ROUNDS}"]


def _ids(output: ModelOutput) -> list[tuple[str, str]]:
    return [(c.function, c.id) for c in output.choices[0].message.tool_calls or []]


def rounds_script(workers: list[str], regulator: str, shell: str = "shell") -> tuple[dict, dict]:
    """The agents' scripts and the plan of every agent's rounds: for each round, the model outputs in order and the
    round's expected end reason, charge, count of rounds used up after it and the tools of its calls."""
    w1, w2, w3 = workers

    def sh(agent: str, tag: str) -> ModelOutput:
        return call(shell, {"command": f"echo {agent} {tag}"}, f"{agent}-{tag}")

    def end(agent: str, tag: str) -> ModelOutput:
        return call(END_ROUND, {}, f"{agent}-{tag}-end")

    four = calls(*[(shell, {"command": f"echo {w1} batch {i}"}) for i in range(1, 5)], call_id=f"{w1}-batch")
    plan = {
        w1: [{"outputs": [call("board_post", {"message": POST}, f"{w1}-post"), call("board_read", {}, f"{w1}-read"),
                          sh(w1, "r1")], "end": "calls", "charge": "work", "used": 1},
             {"outputs": [four], "end": "calls", "charge": "work", "used": 2},
             {"outputs": [call("stop_working", {}, f"{w1}-stop")], "end": "ending", "charge": "stop", "used": 2}],
        w2: [{"outputs": [call("board_read", {}, f"{w2}-read"), sh(w2, "r1"), end(w2, "r1")], "end": "end_round",
              "charge": "work", "used": 1},
             {"outputs": [call("board_status", {}, f"{w2}-status"), end(w2, "r2")], "end": "end_round",
              "charge": "board", "used": 1},
             {"outputs": [sh(w2, "r3"), end(w2, "r3")], "end": "end_round", "charge": "work", "used": 2},
             {"outputs": [call("stop_working", {}, f"{w2}-stop")], "end": "ending", "charge": "stop", "used": 2}],
        w3: [{"outputs": [text(THINKING)], "end": "no_tool_call", "charge": "idle", "used": 1},
             {"outputs": [sh(w3, "r2"), end(w3, "r2")], "end": "end_round", "charge": "work", "used": 2},
             {"outputs": [call("stop_working", {}, f"{w3}-stop")], "end": "ending", "charge": "stop", "used": 2}],
        regulator: [
            {"outputs": [call("board_read", {}, f"{regulator}-read"), sh(regulator, "r1"), end(regulator, "r1")],
             "end": "end_round", "charge": "work", "used": 1},
            {"outputs": [sh(regulator, "r2"), end(regulator, "r2")], "end": "end_round", "charge": "work", "used": 2},
            {"outputs": [sh(regulator, "r3"), end(regulator, "r3")], "end": "end_round", "charge": "work", "used": 3}],
    }
    for rounds in plan.values():
        for r in rounds:
            r["calls"] = [pair for out in r["outputs"] for pair in _ids(out)]
    # one more entry each, never reached: a round past the plan would call it and fail the checks
    script = {a: [o for r in rounds for o in r["outputs"]] + [sh(a, "unreached")] for a, rounds in plan.items()}
    return script, plan


# ------------------------------------------------------------------------------------------------ reading it back

def round_groups(transcript: dict) -> list[dict]:
    """The transcript cut at each round's line: for each round, the line, its tag, the presence lines heard between
    the previous round's last model call and this round's first (on either side of the line), the board's notices
    inside the round, and the round's agent steps. Any other user line fails."""
    groups, pending = [], []
    for step in transcript["trajectory"]["steps"][2:]:
        extra = step.get("extra") or {}
        if step["source"] == "user" and extra.get(ROUND_TAG):
            groups.append({"line": step["message"], "tag": extra[ROUND_TAG], "heard": None, "notices": [],
                           "steps": []})
        elif step["source"] == "user" and extra.get(PRESENCE_TAG):
            pending.append(step["message"])
        elif step["source"] == "user":
            assert groups and step["message"].startswith(NOTICE), ("a user line of no round", step["message"])
            groups[-1]["notices"].append(step["message"])
        else:
            assert groups, ("an agent step before any round line", step)
            if not groups[-1]["steps"]:
                groups[-1]["heard"], pending = pending, []
            groups[-1]["steps"].append(step)
    assert not pending, ("presence lines after the last model call", pending)
    return groups


def _results(step: dict) -> dict[str, str]:
    return {r["source_call_id"]: r["content"] for r in (step.get("observation") or {}).get("results") or []}


def check_rounds(suite, name: str, sample, records: list[dict], plan: dict, workers: list[str], regulator: str,
                 recorder: list[dict]) -> dict:
    """Every rule of the rounds on the exported run: the prompts, the first message's values, each round's line,
    calls, end reason, charge and times, the refused call past N, the live board, the reply with no tool call, and
    the regulator that runs out of rounds and is announced to the one worker left."""
    from tests.smoke.common import first_user, load_transcript, system_text

    w1, w2, w3 = workers
    run = suite.run_json(name)
    timeline = sample.store.get("TurnState:timeline")
    assert run["rules"]["timeline"] == json.loads(json.dumps(timeline)), "run.json's timeline is not the store's"
    assert (run["config"]["rounds"], run["config"]["calls_per_round"]) == (ROUNDS, CALLS), run["config"]
    assert (run["rules"]["round_budget"], run["rules"]["calls_per_round"]) == (ROUNDS, CALLS), run["rules"]
    transcripts = {r["agent"]: load_transcript(r) for r in records}
    assert sorted(transcripts) == sorted(plan), sorted(transcripts)
    board = "board_status, board_read and board_post"
    peers = "board_status, board_read, board_post, get_peer_actions and get_peer_observations"     # the regulator's
    values = rounds_values(SwarmConfig(rounds=ROUNDS, calls_per_round=CALLS))
    out = {}
    for agent, rounds in plan.items():
        t = transcripts[agent]
        system = system_text(t)
        rules = " ".join([TEXTS["rules"], TEXTS["time"], TEXTS["jobs"],
                          TEXTS["free"].format(tools=peers if agent == regulator else board)])
        # the rules with no numbers in the system prompt, every value in the first message
        assert rules in system and not any(ch.isdigit() for ch in rules), (agent, system[-500:])
        assert values not in system and f"{ROUNDS} rounds" not in system and f"{CALLS} tool calls" not in system, agent
        # the paragraph of the rounds' values holds the agent's other values after them: a worker's hand-in delay,
        # the regulator's check rounds (after the task it quotes)
        paragraphs = first_user(t).rstrip().split("\n\n")
        assert any(p.startswith(values) for p in paragraphs), (agent, first_user(t)[-300:])
        own = VALUES["check"].format(rounds="2 rounds") if agent == regulator else \
            VALUES["submit"].format(rounds="2 rounds")
        assert own in paragraphs[-1] and own not in system, (agent, paragraphs[-1])
        assert sum(values in s["message"] for s in t["trajectory"]["steps"] if s["source"] == "user") == 1, agent
        entries = [e for e in timeline if e["agent"] == agent]
        groups = round_groups(t)
        assert len(entries) == len(groups) == len(rounds), (agent, len(entries), len(groups), len(rounds))
        for i, (entry, group, expected) in enumerate(zip(entries, groups, rounds), 1):
            where = (agent, i)
            # the round's line, durable in the conversation, n counting the rounds used up plus one
            n = (entries[i - 2]["used"] if i > 1 else 0) + 1
            assert group["line"] == TEXTS["line"].format(round=n, rounds=ROUNDS), (where, group["line"])
            assert group["tag"] == {"round": i, "n": n, "of": ROUNDS} and entry["round"] == i, (where, group["tag"])
            # the round's calls, in order, as the transcript holds them between its line and the next
            called = [(tc["function_name"], tc["tool_call_id"]) for s in group["steps"]
                      for tc in s.get("tool_calls") or []]
            assert called == expected["calls"], (where, called, expected["calls"])
            assert [(c["tool"], c["id"]) for c in entry["calls"]] == expected["calls"], (where, entry["calls"])
            assert [c["k"] for c in entry["calls"]] == list(range(1, len(entry["calls"]) + 1)), where
            assert all(c["free"] == (c["tool"] in FREE_TOOLS) for c in entry["calls"]), (where, entry["calls"])
            assert len(group["steps"]) == len(expected["outputs"]), (where, len(group["steps"]))     # model calls
            assert (entry["end"], entry["charge"], entry["used"]) == (expected["end"], expected["charge"],
                                                                      expected["used"]), (where, entry)
            assert entry["charged"] == (expected["charge"] in ("work", "idle")), (where, entry)
            # every call's start and end, in order inside the round, and the round's own span around them
            assert entry["t0"] is not None and entry["t1"] is not None and entry["t0"] <= entry["t1"], (where, entry)
            starts = [c["t0"] for c in entry["calls"]]
            assert all(c["t0"] is not None and c["t1"] is not None and c["t0"] <= c["t1"] for c in entry["calls"]), \
                (where, entry["calls"])
            assert starts == sorted(starts), (where, starts)
            assert all(entry["t0"] <= c["t0"] and c["t1"] <= entry["t1"] for c in entry["calls"]), (where, entry)
            counted = [c for c in entry["calls"] if c["tool"] != END_ROUND and not c["refused"]]
            assert len(counted) <= CALLS, (where, entry["calls"])
        if len(entries) > 1:
            assert all(a["t1"] <= b["t0"] for a, b in zip(entries, entries[1:])), (agent, "rounds overlap")
        out[agent] = [(e["end"], e["charge"], len(e["calls"])) for e in entries]

    by_id = {a: {cid: content for g in round_groups(t) for s in g["steps"] for cid, content in _results(s).items()}
             for a, t in transcripts.items()}
    entry_of = {(e["agent"], e["round"]): e for e in timeline}
    # N reached: the fourth call of w1's one reply is refused, and the first three ran
    batch = entry_of[(w1, 2)]["calls"]
    assert [c["refused"] for c in batch] == [False, False, False, True], batch
    spent = TEXTS["spent"].format(calls=CALLS)
    assert [by_id[w1][c["id"]] for c in batch] == [f"{w1} batch 1\n", f"{w1} batch 2\n", f"{w1} batch 3\n", spent], \
        [by_id[w1][c["id"]] for c in batch]
    assert batch[3]["error"] and batch[3]["t0"] == batch[3]["t1"], batch[3]
    # the live board: w1 reads its own post of this round, and w2 reads it later in the same round
    post_line = f"[{w1} #1] {POST}"
    assert post_line in by_id[w1][f"{w1}-read"], by_id[w1][f"{w1}-read"]
    assert post_line in by_id[w2][f"{w2}-read"], by_id[w2][f"{w2}-read"]
    post = entry_of[(w1, 1)]["calls"][0]
    read = entry_of[(w2, 1)]["calls"][0]
    assert post["t1"] <= read["t0"] and entry_of[(w2, 1)]["round"] == 1, (post, read)
    # the reply with no tool call: one agent step with the text and no call, and the next round's line follows it
    [silent] = round_groups(transcripts[w3])[0]["steps"]
    assert silent["message"] == THINKING and not silent.get("tool_calls"), silent
    assert round_groups(transcripts[w3])[1]["heard"] == [], round_groups(transcripts[w3])[1]["heard"]
    # each agent hears every peer enter, in seat order, before its first round
    order = [*workers, regulator]
    for agent, t in transcripts.items():
        heard = round_groups(t)[0]["heard"]
        assert heard == [PRESENCE["entered"].format(agent=a) for a in order if a != agent], (agent, heard)
    # the regulator runs out of rounds after its third; the one worker left hears it before its round 4, once
    ran_out = f"{PRESENCE['rounds'].format(agent=regulator)}\n{PRESENCE['exited'].format(agent=regulator)}"
    w2_groups = round_groups(transcripts[w2])
    left = {w: f"{PRESENCE['stop'].format(agent=w)}\n{PRESENCE['exited'].format(agent=w)}" for w in (w1, w3)}
    assert w2_groups[2]["heard"] == [left[w1]], w2_groups[2]["heard"]                  # w1 stopped before w2's round 3
    assert w2_groups[3]["heard"] == [left[w3], ran_out], w2_groups[3]["heard"]          # w3 and the regulator after it
    assert sum(ran_out in s["message"] for s in transcripts[w2]["trajectory"]["steps"]) == 1
    assert not any(ran_out in s["message"] for a in (w1, w3, regulator) for s in transcripts[a]["trajectory"]["steps"])
    exits = run["rules"]["exits"]
    assert exits[regulator] == {"why": "rounds", "round": 3, "seq": exits[regulator]["seq"]}, exits
    assert {a: exits[a]["why"] for a in workers} == dict.fromkeys(workers, "stop"), exits
    assert run["rules"]["ended"] == {**dict.fromkeys(workers, "stop_working"), regulator: "rounds"}, run["rules"]
    assert run["rules"]["rounds_used"] == {w1: 2, w2: 2, w3: 2, regulator: 3}, run["rules"]["rounds_used"]
    assert run["rules"]["board_rounds"] == {w2: 1}, run["rules"]["board_rounds"]
    # the regulator heard both workers stop in round 3 before its own third round
    assert round_groups(transcripts[regulator])[2]["heard"] == [left[w1], left[w3]], \
        round_groups(transcripts[regulator])[2]["heard"]
    # the provider is offered end_round on every model call
    assert all(END_ROUND in r["tools"] for r in recorder), [r["tools"] for r in recorder]
    ended = {r["agent"]: r["ended_by"] for r in records}
    return {"rounds": out, "ended_by": ended, "exits": exits}
