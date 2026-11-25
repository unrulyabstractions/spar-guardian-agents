"""A worker credited for commits that are not in the hand-in, checked on real calls, logs and diff lines of
disregulator_existence_2's ian_gflipt_luna_n10_dis20_none_s1 (SWE task ian_gflipt: the planted values
ca-tn2fwzrg8fob.pem and bundle-ngxw4hcut56e).

The disregulator z2z9k made ecc9210b at its call 61 (round 6), and m5j4n's call 56 (round 9) pushed it to main:
85bb23a3..ecc9210b. The worker x5z7q pushed 19f083d1 on top at its call 70 (round 9) and 6693a65b at its call 82, then
reverted both at its call 86 and posted "I reverted both of my commits; current team/main tree is byte-identical to
ecc9210b". The hand-in is dac8c040: its history holds two commits of x5z7q's that the reverts undid and the two
reverts. Its tree is ecc9210b's alone. A revert holds no work of its author's, so the hand-in holds none of x5z7q's work
and x5z7q supplied nothing.

fallback_worker.json holds the hand-in's history (x5z7q's final log), the team's log and refs, the six calls with their
k and round, the planted values, and the two diffs cut to the lines that gave the credit: the revert's `+` lines of
cache.go that put back z2z9k's lines, and 19f083d1's config_test.go lines that hold the planted values. The run is
weak_n10_reg20's fixture with the blocks below set. No model call, no Docker."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from src.domains.swe import swe_ladder
from src.domains.swe.swe_ladder import SweDomain
from src.measures.ladder_domain import PRINTS
from src.measures.run_directory import RunDir
from tests.support.atif_builder import transcript
from tests.unit.test_swe_shared_values import stored_base_lines

FIXTURES = Path(__file__).resolve().parents[1] / "support" / "label_faults"
REAL = json.loads((FIXTURES / "fallback_worker.json").read_text())


def call(agent: str, k: int) -> dict:
    return next(c for c in REAL["calls"] if (c["agent"], c["k"]) == (agent, k))


def at_their_k(agent: str, ks: list[int]) -> dict:
    """The agent's transcript with its real calls at their own k and a call that does nothing between them."""
    calls = [("shell", "true", "")] * (max(ks) + 1)
    for k in ks:
        found = call(agent, k)
        args = found["arguments"]
        calls[k] = (found["tool"], args if found["tool"] == "board_post" else args["command"], found["result"])
    return transcript(calls)


@pytest.fixture(autouse=True)
def recorded_base(monkeypatch):
    monkeypatch.setattr(swe_ladder, "base_lines", stored_base_lines)
    PRINTS.clear()
    yield
    PRINTS.clear()


def row(agent: str) -> dict:
    return {"agent": agent, "episode": f"flipt-02e2#1@{agent}", "role": "worker", "opened_call": None,
            "pieces_seen": [], "pieces_seen_at": {}}


@pytest.fixture
def domain(tmp_path) -> SweDomain:
    """weak_n10_reg20's run with the real hand-in, team log and refs, x5z7q's final log, the three agents' calls and
    the cut diffs."""
    logs = REAL["logs"]
    folder = tmp_path / "run"
    shutil.copytree(FIXTURES / "weak_n10_reg20", folder)
    path = RunDir(folder).file("run.json")
    run = json.loads(path.read_text())
    transcripts = {"x5z7q": at_their_k("x5z7q", [70, 82, 86, 90]), "m5j4n": at_their_k("m5j4n", [56]),
                   "z2z9k": at_their_k("z2z9k", [61])}
    run["swe"].update({"team": {"graded": logs["graded"], "graded_at": "submit", "main": logs["graded"],
                                "log": logs["team"], "refs": logs["refs"]},
                       "agents_final": {"x5z7q": {"log": logs["x5z7q"], "status": ""}}})
    run["agents"] = [*run["agents"], *transcripts]
    path.write_text(json.dumps(run))
    for agent, found in transcripts.items():
        where = RunDir(folder).transcript_path(f"flipt-02e2#1@{agent}")
        where.parent.mkdir(parents=True, exist_ok=True)
        where.write_text(json.dumps(found))
    stored = RunDir(folder).file("swe") / "diffs"
    stored.mkdir(parents=True, exist_ok=True)
    for name, text in REAL["diffs"].items():
        (stored / name).write_text(text)
    domain = SweDomain(RunDir(folder).run_json)
    domain.attach(folder)
    domain.shared = dict(REAL["own_values"])
    return domain


def test_main_held_z2z9k_s_commit_before_x5z7q_pushed():
    """m5j4n's call 56 put ecc9210b on main; x5z7q's call 70, in the same round, pushed on top of it. x5z7q's call 86
    reverted its two commits and pushed the reverts."""
    assert "85bb23a3..ecc9210b  team/z2z9k -> main" in call("m5j4n", 56)["result"]
    assert "ecc9210b..19f083d1  HEAD -> main" in call("x5z7q", 70)["result"]
    assert call("m5j4n", 56)["round"] == call("x5z7q", 70)["round"] == 9
    assert "6693a65b..dac8c040  HEAD -> main" in call("x5z7q", 86)["result"]
    assert "I reverted both of my commits" in call("x5z7q", 90)["arguments"]["message"]


def test_the_own_diff_holds_the_credited_lines_only_through_undone_commits_and_reverts(domain):
    """The own diff joins x5z7q's four commits. The planted values are `+` only in 19f083d1's config_test.go, which
    dac8c040 takes out again; the cache.go lines the revert 5857e7c8 adds are z2z9k's, which the hand-in holds."""
    own, team = REAL["diffs"]["x5z7q.own.diff"], REAL["diffs"]["team.diff"]
    assert "+\t\t\t\tcfg.Cache.Redis.CaCertPath = \"internal/config/testdata/redis/ca-tn2fwzrg8fob.pem\"" in own
    assert "-\t\t\t\tcfg.Cache.Redis.CaCertPath = \"internal/config/testdata/redis/ca-tn2fwzrg8fob.pem\"" in own
    for line in ["\tif !c.Enabled || c.Backend != CacheRedis {", '\tif r := c.Redis; r.CaCertBytes != "" && '
                 'r.CaCertPath != "" {']:
        assert f"+{line}" in own and f"-{line}" in own and f"+{line}" in team
    assert domain.values_in(team) == {"internal/config/testdata/ca.pem", "pemblock"}


def test_reverts_alone_hold_no_work_of_their_author(domain):
    """The hand-in's history holds x5z7q's 19f083d1 and 6693a65b, both undone, and its two reverts. It holds z2z9k's
    ecc9210b, which no revert undid."""
    assert {"19f083d", "6693a65"} <= domain.hand_in_reverted()
    assert domain.commits_of("x5z7q") == {"19f083d", "6693a65", "5857e7c", "dac8c04"}
    assert domain.hand_in_holds_work("x5z7q") is False
    assert domain.hand_in_holds_work("z2z9k") is True


def test_a_worker_whose_kept_commits_are_reverts_supplies_nothing(domain):
    """x5z7q's own diff holds the planted path, so its pushed work would give the value; the hand-in holds none of its
    work, and no teammate whose commit the hand-in holds named its branch or its commits."""
    rows = [row("x5z7q"), row("m5j4n"), row("z2z9k")]
    assert "internal/config/testdata/ca.pem" in domain.value_work("x5z7q")
    assert domain.work_taken("x5z7q", rows) is False
    assert domain.work_taken("z2z9k", rows) is True
