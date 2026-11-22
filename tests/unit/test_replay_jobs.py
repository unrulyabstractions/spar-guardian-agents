"""The replay of a run made since the round clock (src/replay/replay_jobs.py): a call the run kept as a job runs on as
that job on the run's clock, its job_output reads and its job_stop play on it, an ordinary call has no fixed limit,
and a replay that cannot reproduce the record says so. No Docker: the box runs every exec on this machine, as
command_jobs' own scripts would run in a container."""
from __future__ import annotations

import os
import subprocess
import time

import pytest

from src.replay import replay_jobs
from src.replay.recorded_calls import RecordedCall
from src.replay.replay_jobs import JobPlayer, Place, jobs_era
from src.replay.replay_run import OutputCut, play_paced
from src.replay.replay_sandbox import Exec

KEPT = "Still running as job {job}: it ran past its time."


class LocalReplayBox:
    """ReplaySandbox.run on this machine: each exec in a session of its own, as docker makes every exec a session
    leader, and a command killed by a signal exits 128 + its number, as docker exec reports it; service, user and
    workdir are recorded, never taken."""

    def __init__(self) -> None:
        self.runs: list[tuple[str, list[str]]] = []

    def run(self, service, argv, *, stdin=None, timeout=1800, workdir=None, user=None) -> Exec:
        self.runs.append((service, list(argv)))
        start = time.time()
        done = subprocess.run(argv, input=stdin, capture_output=True, timeout=timeout, start_new_session=True,
                              stdin=None if stdin is not None else subprocess.DEVNULL)
        rc = 128 - done.returncode if done.returncode < 0 else done.returncode
        return Exec(rc, done.stdout, done.stderr, time.time() - start)


class LocalReplayer:
    agents = ["a01", "a02"]

    def __init__(self, jobs: JobPlayer) -> None:
        self.jobs = jobs

    def in_sandbox(self, call):
        return call.tool == "shell" or self.jobs.handles(call)

    def shared(self, call):
        return False

    @staticmethod
    def command(call):
        return str(call.arguments.get("command") or "")

    def place(self, call):
        return Place("m", None, None, {}, ["bash", "-c", self.command(call)])

    def execute(self, box, call):
        return self.jobs.execute(box, call, self)

    def after(self, box, call, output):
        return output, {}, [], []

    def team_state(self, box):
        return None


def shell(n, agent, k, command, result, t0, t1, cid=None):
    return RecordedCall(n=n, agent=agent, k=k, id=cid or f"c{n}", tool="shell", arguments={"command": command},
                        result=result, error=None, t0=t0, t1=t1)


def job_call(n, agent, k, tool, job, result, t0):
    return RecordedCall(n=n, agent=agent, k=k, id=f"c{n}", tool=tool, arguments={"job": job}, result=result,
                        error=None, t0=t0, t1=t0 + 0.1)


T0 = 1_800_000_000.0


def recorded(stop_exit: str = "sleep 2; echo DONE; exit 4"):
    """a01's `sleep 600` kept as j1, read and stopped; a02's job j2 that exits with 4 between its reads; an ordinary
    call of a02 beside them. Times are the run's."""
    calls = [
        shell(1, "a01", 0, "echo START; sleep 600", KEPT.format(job="j1") + " Its output so far:\nSTART\n",
              T0, T0 + 1.0, "kept1"),
        shell(2, "a02", 0, stop_exit, KEPT.format(job="j2") + " It has no output so far.", T0 + 0.05, T0 + 1.05,
              "kept2"),
        job_call(3, "a01", 1, "job_output", "j1", "Job j1 is still running. It has no new output.", T0 + 1.2),
        shell(4, "a02", 1, "echo hi", "hi\n", T0 + 1.3, T0 + 1.4),
        job_call(5, "a01", 2, "job_stop", "j1", "Job j1 was stopped. It has no new output.", T0 + 1.5),
        job_call(6, "a02", 2, "job_output", "j2", "Job j2 exited with code 4. Its new output:\nDONE\n", T0 + 3.5),
    ]
    run_json = {"config": {"round_time_s": 1, "tool_output_bytes": 65536, "tool_output_cut": "middle"},
                "jobs": [{"id": "j1", "agent": "a01", "tool": "shell", "machine": "m", "call_id": "kept1",
                          "started": T0, "ended": T0 + 1.6, "status": "stopped", "exit_code": 137},
                         {"id": "j2", "agent": "a02", "tool": "shell", "machine": "m", "call_id": "kept2",
                          "started": T0 + 0.05, "ended": T0 + 2.1, "status": "exited", "exit_code": 4}]}
    return calls, run_json


def replayed(tmp_path, calls, run_json):
    jobs = JobPlayer(run_json, calls, service_of=lambda agent: "m", top=str(tmp_path / "jobs"))
    box = LocalReplayBox()
    started = time.time()
    try:
        played = play_paced(box, LocalReplayer(jobs), calls, OutputCut.of(run_json["config"]), jobs)
    finally:
        jobs.end(box)
    return jobs, played, time.time() - started


@pytest.mark.skipif(not os.path.exists("/bin/bash"), reason="needs bash")
def test_a_kept_call_runs_on_as_its_job_and_its_reads_and_stop_play_on_it(tmp_path):
    calls, run_json = recorded()
    jobs, played, seconds = replayed(tmp_path, calls, run_json)
    assert {n: r["match"] for n, r in played.items()} == {n: "exact" for n in range(1, 7)}, \
        {n: (r["match"], r["call"].result, r.get("output")) for n, r in played.items()}
    report = jobs.report(played)
    assert report["problems"] == []
    assert [(j["id"], j["replayed"]["status"], j["replayed"]["exit_code"]) for j in report["jobs"]] == \
        [("j1", "stopped", 137), ("j2", "exited", 4)]
    assert seconds < 15, "the stopped sleep 600 ran on, or a call waited out a fixed limit"


@pytest.mark.skipif(not os.path.exists("/bin/bash"), reason="needs bash")
def test_a_kept_call_that_ends_in_time_in_the_replay_is_a_problem(tmp_path):
    calls, run_json = recorded(stop_exit="echo DONE; exit 4")
    jobs, played, _ = replayed(tmp_path, calls, run_json)
    whys = [p["why"] for p in jobs.report(played)["problems"]]
    assert any("ended in time in the replay" in w for w in whys)
    assert any("never started" in w for w in whys)


@pytest.mark.skipif(not os.path.exists("/bin/bash"), reason="needs bash")
def test_an_ordinary_call_has_no_fixed_limit_but_one_far_past_its_record_is_stopped(tmp_path, monkeypatch):
    monkeypatch.setattr(replay_jobs, "OVERRUN_S", 0.5)
    calls = [shell(1, "a01", 0, "sleep 2; echo late", "late\n", T0, T0 + 1.8),
             shell(2, "a02", 0, "sleep 600", "", T0 + 0.1, T0 + 0.2)]
    run_json = {"config": {"round_time_s": 0}}
    jobs, played, seconds = replayed(tmp_path, calls, run_json)
    assert played[1]["match"] == "exact"
    assert played[2]["match"] == "differs"
    assert [p["n"] for p in jobs.report(played)["problems"]] == [2]
    assert seconds < 10


def test_only_a_run_made_since_the_round_clock_plays_through_the_job_player():
    assert jobs_era({"config": {"round_time_s": 0}})
    assert not jobs_era({"config": {"rounds": 8}})
    assert not jobs_era({})


@pytest.mark.skipif(not os.path.exists("/bin/bash"), reason="needs bash")
def test_a_listing_of_the_job_folder_replays_the_names_the_run_saw(tmp_path):
    """The replay names each command's folder as the run did (command_jobs.call_name), so a02's listing, which in
    the run showed a01's job and its own call, matches exactly."""
    top = tmp_path / "jobs"
    calls = [shell(1, "a01", 0, "echo START; sleep 600", KEPT.format(job="j1") + " Its output so far:\nSTART\n",
                   T0, T0 + 1.0, "kept1"),
             shell(2, "a02", 0, f"ls {top}", "a01-1\na02-1\n", T0 + 1.2, T0 + 1.3),
             job_call(3, "a01", 1, "job_stop", "j1", "Job j1 was stopped. It has no new output.", T0 + 1.5)]
    run_json = {"config": {"round_time_s": 1, "tool_output_bytes": 65536, "tool_output_cut": "middle"},
                "jobs": [{"id": "j1", "agent": "a01", "tool": "shell", "machine": "m", "call_id": "kept1",
                          "started": T0, "ended": T0 + 1.6, "status": "stopped", "exit_code": 137}]}
    jobs, played, _ = replayed(tmp_path, calls, run_json)
    assert played[2]["match"] == "exact", (played[2]["call"].result, played[2].get("output"))
    assert jobs.report(played)["problems"] == []
