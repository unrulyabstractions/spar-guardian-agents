"""Seed packs (SwarmConfig.seed_pack) on the real sandbox, with the mock model and no model spend.

    uv run python -m tests.smoke.seeded_runs [--only swe]

swe       flipt-02e2 under the jigsaw honeypot, two agents, swe/exp0_pooled_and_passed: the previous team's three posts
          stand on the board before round 1 and the notes sit in /tmp of each agent's machine, root's, outside the
          repository.
          a01 reads the board first, a02 the notes first; each then reads the other.

A file under a pack's files/home/ goes to each agent's home, the agent's own; tests/unit/test_seed_packs.py checks where
it goes and who owns it.

Each run's run.json records the pack, the previous team's IDs, every post and file and where each was planted, and
the board's export marks the seeded posts. The previous team is on no roster: no read counts it as a teammate, and the
code record gives no agent a seeded exposure as a teammate's. The runs go under runs/study_smoke/smoke_seeds/<stamp>/
and the eval logs under logs/study_smoke/smoke_seeds/<stamp>/.
"""
from __future__ import annotations

from src.configuration.seed_packs import SEEDED
from src.domains.swe import SWE
from src.judging.cheatbench_judge import judge_spec
from src.swarm.turn_rules import message_parts
from src.ui.transcript_index import board_view
from src.utils.json_files import read_json_dir
from tests.smoke import common
from tests.smoke.common import (
    Suite,
    assert_ok,
    by_agent,
    load_transcript,
    main,
    results_text,
    task_for,
    tool_calls_of,
)
from tests.support.mock_model import call, scripted

SUITE = Suite("smoke_seeds")
NOTES = "previous_team_notes.md"


def sh(cmd: str, cid: str):
    return call("shell", {"command": cmd}, cid)


def board(cid: str):
    return call("board_read", {}, cid)


def stop(cid: str):
    return call("stop_working", {}, cid)


def check_seed(name: str, record: dict, agents: list[str], planted: dict[str, str]) -> dict:
    """run.json's seed block is the task's record plus where each file went ({agent: path}) and the seeded posts'
    board ids; the board's export holds the seeded posts first, marked, under the previous team's IDs; the browser's
    board view marks them; no agent's read counts a seeded author; the code record names no seeded exposure as a
    teammate's."""
    run = SUITE.run_json(name)
    seed = run["seed"]
    assert {k: seed[k] for k in record} == common.plain(record), seed
    assert {p["agent"]: p["path"] for p in seed["planted"]} == planted, seed["planted"]
    [comm] = read_json_dir(SUITE.runs / name / "processed/communication").values()
    seeded = [m for m in comm["messages"] if m.get("invocation_id") == SEEDED]
    assert [(m["sender"], m["content"]) for m in seeded] == [(p["author"], p["text"]) for p in record["posts"]]
    assert comm["messages"][:len(seeded)] == seeded and seed["post_ids"] == [m["id"] for m in seeded]
    assert not set(record["authors"]) & set(agents)
    [view] = board_view(SUITE.runs / name, {a: "worker" for a in agents})
    assert [m["seeded"] for m in view["messages"]] == [True] * len(seeded) + [False] * (len(view["messages"]) -
                                                                                       len(seeded))
    senders = run["rules"].get("read_senders") or {}
    assert not {s for counts in senders.values() for s in counts} & set(record["authors"]), senders
    rows = SUITE.ladder(name)
    assert all(row["exposed_source"] != "teammate_post" or not row["exposed"] for row in rows.values()), rows
    return {"pack": seed["pack"], "authors": seed["authors"], "planted": seed["planted"],
            "exposure": {a: (r["exposed"], r["exposed_source"]) for a, r in rows.items()}}


def scenario_swe() -> dict:
    pack, path = "swe/exp0_pooled_and_passed", f"/tmp/{NOTES}"
    read_notes = f"cat {path}; stat -c '%U %a' {path}; cd /app && git status --porcelain | wc -l"
    script = {"a01": [board("a1"), sh(read_notes, "a2"), stop("a3")],
              "a02": [sh(read_notes, "b1"), board("b2"), stop("b3")]}
    task = task_for(SWE("flipt-02e2", "jigsaw", grade=False), n_agents=2, concurrent_rounds=False,
                    turn_order="round_robin", seed_pack=pack)
    record = task.dataset[0].metadata["cheatbench"]["seed"]
    log, _, records = SUITE.with_spec(judge_spec(SWE.task_dir, SWE.name)).run("swe", task, scripted(script))
    assert_ok(log)
    recs = by_agent(records)
    assert sorted(recs) == ["a01", "a02"], recs
    notes = record["files"][0]["text"]
    for agent, rec in recs.items():
        t = load_transcript(rec)
        text = results_text(t)
        assert [c[0] for c in tool_calls_of(t)] == [c.message.tool_calls[0].function for c in script[agent]]
        assert notes.strip() in text and "root 644\n0\n" in text, (agent, text[-1500:])
        for post in record["posts"]:
            assert f"[{post['author']} #" in text and post["text"] in text, (agent, post)
        # the first round's message holds the board part, which names each seeded post's author
        first = next(s for s in common.steps_of(t) if s["source"] == "user" and
                     ((s.get("extra") or {}).get("swarm_round") or {}).get("round") == 1)
        notice = message_parts(first["message"], first.get("extra"))["notice"]
        assert notice and all(f"- {post['author']}: " in notice["text"] for post in record["posts"]), \
            (agent, first["message"])
    return check_seed("swe", record, ["a01", "a02"], {"a01": path, "a02": path})


SCENARIOS = {"swe": scenario_swe}


if __name__ == "__main__":
    raise SystemExit(main(SUITE, SCENARIOS, __doc__))
