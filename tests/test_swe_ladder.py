"""The code ladder on SWE (src/domains/swe/swe_ladder.py) and the records it reads: every team branch's diff from the
grader (src/domains/swe/swe_scoring.py) written beside the graded diffs. No model calls, no Docker."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from src.domains.swe import swe_scoring
from src.domains.swe.swe_scoring import branch_file
from src.domains.swe.swe_task import SWE


class FakeBox:
    """A grader that answers `git for-each-ref` with the given refs and records every command."""

    def __init__(self, refs: list[str]) -> None:
        self.refs, self.commands = refs, []

    async def exec(self, argv, timeout=None, input=None):
        cmd = argv[-1]
        self.commands.append(cmd)
        out = "\n".join(self.refs) + "\n" if "for-each-ref" in cmd else ""
        return SimpleNamespace(returncode=0, stdout=out, stderr="")


def test_the_grader_keeps_every_team_branch_but_main_as_a_diff(monkeypatch):
    async def read_diff(box, path):
        return f"+{box.commands[-1].split()[-3]}\n".encode()        # the ref the last diff command named

    monkeypatch.setattr(swe_scoring, "_read_diff", read_diff)
    box = FakeBox(["refs/grade/team/a01", "refs/grade/team/main", "refs/grade/team/rd/redis-new-client"])
    branches = asyncio.run(swe_scoring._team_branches(box, "base0"))
    assert branches == {"a01": "+refs/grade/team/a01\n",
                        "rd/redis-new-client": "+refs/grade/team/rd/redis-new-client\n"}
    assert not any("refs/grade/team/main" in cmd for cmd in box.commands if " diff " in cmd)
    # a branch name the agents chose is quoted in the command
    box = FakeBox(["refs/grade/team/x;rm"])
    asyncio.run(swe_scoring._team_branches(box, "base0"))
    assert "'refs/grade/team/x;rm'" in box.commands[-1]


def test_each_team_branch_is_written_beside_the_graded_diffs(tmp_path):
    assert branch_file("rd/redis-new-client") == "team.rd~redis-new-client.diff"
    md = {"targets": {"team": {"diff_text": "+main\n"}}, "agents": {"a01": {"own_diff_text": "+own\n"}},
          "team_branches": {"a01": "+a01\n", "rd/x": "+x\n", "broken": None}}
    view = SimpleNamespace(scores={SWE.scorer: SimpleNamespace(metadata=md)})
    SWE.write_files(view, tmp_path)
    diffs = tmp_path / "swe" / "diffs"
    assert sorted(p.name for p in diffs.iterdir()) == ["a01.own.diff", "team.a01.diff", "team.diff",
                                                       "team.rd~x.diff"]
    assert (diffs / "team.rd~x.diff").read_text() == "+x\n"
