"""tools/docent on a copy of the generated riemann_minimal (three workers, a02 and a03 judged cheated by CheatBench's
judge, two board posts), with the run environment stood in for: the dry run, the refusals without a usable key, and the
upload path against a stand-in client."""
from __future__ import annotations

import json

import pytest

from src.utils.json_files import read_jsonl, write_jsonl
from tools.docent import docent_command as command
from tools.docent import docent_convert
from tools.docent.docent_upload import key_problem
from tools.tool_dispatch import main


@pytest.fixture
def no_key(monkeypatch, tmp_path):
    """No DOCENT_API_KEY, an empty home and a cwd with no docent.env above it."""
    monkeypatch.delenv("DOCENT_API_KEY", raising=False)
    monkeypatch.delenv("DOCENT_PROFILE", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)


def test_key_problem_names_the_three_places():
    message = key_problem(None)
    for place in ("DOCENT_API_KEY", "~/.docent/docent.env", "docent.env in the current directory", "wins over"):
        assert place in message
    assert "placeholder" in key_problem("<your key>")
    assert key_problem("dk_real_looking_key") is None


def test_upload_without_key_exits_2(no_key, capsys):
    assert main(["docent", "riemann_minimal"]) == 2
    assert "no Docent API key found" in capsys.readouterr().err


LADDERS = {"a01": ("none", "write", "none", False), "a02": ("tempted", "none", "cooperation", False),
           "a03": ("attempted", "write", "cooperation", True), "team": ("attempted", "read", "cooperation", True)}
"""Each row's three ladders and cheated (RUNGS: cheating_rung, coordination_level, collusion_rung, cheated), as
src/measures/code_ladder.py ladders gives them."""
RUNGS = docent_convert.FIELDS[:4]
SCORES = {"a01": (0, 10), "a02": (None, None), "a03": (85, 40), "team": (85, 40)}
"""Each agent's judge label scores, disclosure_score and manipulation_score (a02 has no parsed row), and the team's
highest, as judge_labels.labels gives them."""


def evidence(score: int) -> dict:
    return {"score": score, "excerpt": "x" if score else "", "source": "post" if score else "none", "reason": "r",
            "located": None}


ANSWER = {"ladders": {"agents": {a: dict(zip(RUNGS, LADDERS[a])) for a in ("a01", "a02", "a03")},
                      "team": dict(zip(RUNGS, LADDERS["team"]))},
          "labels": {"judge": "gpt-6-luna",
                     "agents": {a: None if None in SCORES[a] else dict(zip(docent_convert.LABELS,
                                                                           map(evidence, SCORES[a])))
                                for a in ("a01", "a02", "a03")},
                     "team": dict(zip(docent_convert.LABELS, SCORES["team"])), "influencers": {}}}
"""What the run environment answers for the run (docent_convert.COMPUTE)."""


@pytest.fixture
def run_python(monkeypatch, tmp_path, riemann_copy):
    """A stand-in for the run environment's Python that answers ANSWER for riemann_copy, and the code record the
    measures read beside it (signals.jsonl), written without the ladders."""
    write_jsonl(riemann_copy / "signals.jsonl", [{"row": "agent", "agent": a} for a in ("a01", "a02", "a03")]
                + [{"row": "team"}])
    stand_in = tmp_path / "run_python"
    stand_in.write_text(f"#!/bin/sh\ntest \"$1\" = -c && test \"$3\" = {riemann_copy.resolve()} && "
                        f"echo '{json.dumps(ANSWER)}'\n")
    stand_in.chmod(0o755)
    monkeypatch.setattr(docent_convert, "RUN_PYTHON", stand_in)
    docent_convert.measures.cache_clear()
    return stand_in


def test_dry_run_on_riemann(no_key, riemann_copy, run_python):
    before = (riemann_copy / "signals.jsonl").read_bytes()
    assert main(["docent", str(riemann_copy), "--dry-run", "--board"]) == 0
    assert (riemann_copy / "signals.jsonl").read_bytes() == before        # computed, never written
    out = riemann_copy / "tools" / "docent"
    summary = json.loads((out / "summary.json").read_text())
    agent_runs = json.loads((out / "agent_runs.json").read_text())
    assert summary["tool"] == "docent" and summary["dry_run"] is True
    assert summary["n_agent_runs"] == len(agent_runs) == 4
    assert [run["id"] for run in agent_runs] == [row["id"] for row in summary["agent_runs"]]
    judge = {row["agent"]: row for row in read_jsonl(riemann_copy / "judges" / "cheatbench.gpt-6-sol.jsonl")}
    for agent_run in agent_runs[:3]:
        metadata = agent_run["metadata"]
        agent = metadata["agent"]
        assert metadata["judge_cheated"] == judge[agent]["cheated"]
        assert metadata["attempted_access"] == judge[agent]["attempted_access"]
        assert metadata["judge_summary"] == judge[agent]["summary"]
        assert metadata["roster"] == ["a01", "a02", "a03"]
        steps = json.loads(next((riemann_copy / "trajectories").glob(f"*@{agent}/transcript.json")).read_text())
        steps = steps["trajectory"]["steps"]
        results = sum(len((step.get("observation") or {}).get("results") or []) for step in steps)
        messages = agent_run["transcripts"][0]["messages"]
        assert len(messages) == len(steps) + results
        assistant = [message for message in messages if message["role"] == "assistant"]
        assert all(message["metadata"]["round"] is not None for message in assistant)
    assert [run["metadata"]["judge_cheated"] for run in agent_runs[:3]] == [False, True, True]
    assert agent_runs[0]["metadata"]["judge_cheated_by_agent"] == {"a01": False, "a02": True, "a03": True}
    # the measures under the project's names, which a run has whether or not a judge read it, and no older name
    for agent_run in agent_runs:
        metadata = agent_run["metadata"]
        assert tuple(metadata[f"team_{field}"] for field in docent_convert.TEAM_FIELDS) == LADDERS["team"] + \
            SCORES["team"]
        assert not {"code_rung", "team_code_rung", "team_collaboration", "cheated_by_agent", "awareness", "scheming",
                    "disclosure", "manipulation"} & set(metadata)
        if metadata["kind"] == "agent":
            agent = metadata["agent"]
            assert tuple(metadata[field] for field in docent_convert.FIELDS) == LADDERS[agent] + SCORES[agent]
            cheating, coordination, collusion, cheated = LADDERS[agent]
            disclosure, manipulation = SCORES[agent]
            assert (f"cheating={cheating}, cheated={cheated}, coordination={coordination}, collusion={collusion}, "
                    f"disclosure_score={disclosure}, manipulation_score={manipulation}" in agent_run["description"])
    assert summary["agent_runs"][2]["cheated"] is True                   # the Cheating ladder's, a03 at attempted
    board = agent_runs[3]
    assert board["metadata"]["kind"] == "board"
    posts = board["transcripts"][0]["messages"][1:]
    assert [post["metadata"]["sender"] for post in posts] == ["a03", "a01"]
    assert posts[0]["content"].startswith("a03 (worker) posted #1 in round 12:")
    assert "code_ladder.ladders(run)" in docent_convert.COMPUTE and "judge_labels.labels(run)" in docent_convert.COMPUTE


@pytest.mark.skipif(not docent_convert.RUN_PYTHON.is_file(), reason="no run environment in this checkout")
def test_the_run_environment_answers_for_a_real_run(riemann_copy):
    """The real run environment (RUN_PYTHON) on the generated run with a code record that holds the ladders: every
    agent carries every field of FIELDS, its ladders as recorded and its judge label scores as the measure reads them
    (None: no judge has read the run), and nothing is written into the run."""
    rows = [{"row": "agent", "agent": a, **dict(zip(RUNGS[:3], LADDERS[a]))} for a in ("a01", "a02", "a03")]
    write_jsonl(riemann_copy / "signals.jsonl", [*rows, {"row": "team", **dict(zip(RUNGS[:3], LADDERS["team"]))}])
    before = sorted((p, p.stat().st_mtime_ns) for p in riemann_copy.rglob("*") if p.is_file())
    docent_convert.measures.cache_clear()
    agents, team = docent_convert.measures(riemann_copy)
    assert {a: tuple(fields[f] for f in docent_convert.FIELDS) for a, fields in agents.items()} == {
        a: LADDERS[a] + (None, None) for a in ("a01", "a02", "a03")}
    assert tuple(team[f] for f in docent_convert.TEAM_FIELDS) == LADDERS["team"] + (None, None)
    assert sorted((p, p.stat().st_mtime_ns) for p in riemann_copy.rglob("*") if p.is_file()) == before


def test_a_folder_without_run_json_has_no_measures(tmp_path):
    assert docent_convert.measures(tmp_path / "never_read") == ({}, {})


class StandInClient:
    """Records what an upload would send; fails on the upload numbered by fail_on."""

    frontend_url = "https://docent.example"

    def __init__(self, fail_on: int | None = None):
        self.uploads = []
        self.fail_on = fail_on

    def create_collection(self, name, description):
        return "collection-1"

    def get_collection(self, collection_id):
        return {"id": collection_id}

    def add_agent_runs(self, collection_id, agent_runs):
        if self.fail_on == len(self.uploads):
            raise RuntimeError("stand-in upload failure")
        self.uploads.append((collection_id, agent_runs))
        return {"status": "success", "total_runs_added": len(agent_runs), "job_ids": ["job-1"]}


def test_upload_records_collection_and_ids(monkeypatch, capsys, riemann_copy):
    client = StandInClient()
    monkeypatch.setattr(command, "discovered_key", lambda: "dk_stand_in")
    monkeypatch.setattr(command, "connect", lambda: client)
    assert main(["docent", str(riemann_copy)]) == 0
    summary = json.loads((riemann_copy / "tools" / "docent" / "summary.json").read_text())
    collection_id, uploaded = client.uploads[0]
    assert collection_id == summary["collection_id"] == "collection-1"
    assert summary["dry_run"] is False
    assert summary["agent_run_ids"] == [agent_run.id for agent_run in uploaded]
    assert summary["collection_url"] == "https://docent.example/dashboard/collection-1"
    assert "https://docent.example/dashboard/collection-1" in capsys.readouterr().out
    assert main(["docent", str(riemann_copy), "--collection-id", "existing"]) == 0
    assert client.uploads[1][0] == "existing"


def test_failed_upload_names_the_collection(monkeypatch, capsys, riemann_copy):
    client = StandInClient(fail_on=0)
    monkeypatch.setattr(command, "discovered_key", lambda: "dk_stand_in")
    monkeypatch.setattr(command, "connect", lambda: client)
    assert main(["docent", str(riemann_copy)]) == 1
    err = capsys.readouterr().err
    assert "stand-in upload failure" in err and "--collection-id collection-1" in err
    assert not (riemann_copy / "tools" / "docent" / "summary.json").exists()
