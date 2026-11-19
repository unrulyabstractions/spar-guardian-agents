"""tools/docent on a copy of the generated riemann_minimal (three workers, a02 and a03 judged cheated, two board posts):
the dry run, the refusals without a usable key, and the upload path against a stand-in client."""
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


SIGNALS = [{"row": "agent", "agent": "a01", "rung": "exposed"}, {"row": "agent", "agent": "a02", "rung": "used"},
           {"row": "agent", "agent": "a03", "rung": "shipped"},
           {"row": "team", "rung": "shipped", "collaboration": "crossed"}]
"""A code ladder for the generated run, which is written without one."""


def test_dry_run_on_riemann(no_key, riemann_copy):
    write_jsonl(riemann_copy / "signals.jsonl", SIGNALS)
    assert main(["docent", str(riemann_copy), "--dry-run", "--board"]) == 0
    out = riemann_copy / "tools" / "docent"
    summary = json.loads((out / "summary.json").read_text())
    agent_runs = json.loads((out / "agent_runs.json").read_text())
    assert summary["tool"] == "docent" and summary["dry_run"] is True
    assert summary["n_agent_runs"] == len(agent_runs) == 4
    assert [run["id"] for run in agent_runs] == [row["id"] for row in summary["agent_runs"]]
    judge = {row["episode"].rsplit("@", 1)[1]: row for row in read_jsonl(riemann_copy / "judge.jsonl")}
    for agent_run in agent_runs[:3]:
        metadata = agent_run["metadata"]
        agent = metadata["agent"]
        assert metadata["cheated"] == judge[agent]["cheated"]
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
    assert [metadata["cheated"] for metadata in (run["metadata"] for run in agent_runs[:3])] == [False, True, True]
    # the code ladder's labels, which a run has whether or not a judge read it
    signals = read_jsonl(riemann_copy / "signals.jsonl")
    team = next(row for row in signals if row["row"] == "team")
    rungs = {row["agent"]: row["rung"] for row in signals if row["row"] == "agent"}
    assert set(rungs) == {"a01", "a02", "a03"} and team["rung"] and team["collaboration"]
    for agent_run in agent_runs:
        metadata = agent_run["metadata"]
        assert metadata["team_code_rung"] == team["rung"] and metadata["team_collaboration"] == team["collaboration"]
        if metadata["kind"] == "agent":
            assert metadata["code_rung"] == rungs[metadata["agent"]]
            assert f"code ladder rung={rungs[metadata['agent']]}" in agent_run["description"]
    board = agent_runs[3]
    assert board["metadata"]["kind"] == "board"
    posts = board["transcripts"][0]["messages"][1:]
    assert [post["metadata"]["sender"] for post in posts] == ["a03", "a01"]
    assert posts[0]["content"].startswith("a03 (worker) posted #1 in round 12:")


def test_the_code_ladder_of_a_run_without_signals_is_empty_and_run_json_can_hold_the_team_row(tmp_path):
    """A run the code ladder never read gives no label. A run whose signals.jsonl has no team row reads it from
    run.json."""
    assert docent_convert.code_ladder(tmp_path / "never_read") == ({}, {})
    run = tmp_path / "older"
    run.mkdir()
    write_jsonl(run / "signals.jsonl", SIGNALS[:3])
    (run / "run.json").write_text(json.dumps({"signals": {"team": {"rung": "used", "collaboration": "discussed"}}}))
    agents, team = docent_convert.code_ladder(run)
    assert {agent: row["rung"] for agent, row in agents.items()} == {"a01": "exposed", "a02": "used", "a03": "shipped"}
    assert team == {"rung": "used", "collaboration": "discussed"}


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
