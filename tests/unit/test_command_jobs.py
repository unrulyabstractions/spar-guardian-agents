"""Command calls with no time limit of their own and the jobs they become (src/sandbox/command_jobs.py), run for real on
this machine (tests/support/local_box.py): a command that ends in its round answers as Inspect's exec did, one still
running when the round's time runs out becomes a job that the agent reads and stops in later rounds, the jobs still
running at the end of the run are killed, and the round ends on time (turn_rules round_time). No Docker, no model call.
"""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import signal
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest
from inspect_ai.dataset import Sample
from inspect_ai.tool import ToolDef, ToolError
from inspect_ai.tool._tools._execute import bash, python
from inspect_ai.util import ExecResult
from orbit import AgentSpec, SetupConfig, ToolFactory, multi_agent_task
from orbit.configs.experiment import ExperimentConfig

from src.configuration.settings import load_prompts
from src.export import _export_run_blocks as blocks
from src.sandbox import agent_tools, command_jobs
from src.sandbox import peer_shell as peer
from src.swarm import turn_rules as rules
from src.swarm.board import board_execution, board_scheduler
from src.swarm.end_round import end_round_bindings
from src.swarm.tool_output_caps import capped_bindings
from src.swarm.turn_rules import _turn_rounds, turn_activation
from src.swarm.turn_rules._turn_state import Live
from tests.support import turn_rules_harness as harness
from tests.support.fakes import FakeStore
from tests.support.local_box import LocalBox
from tests.support.mock_model import calls
from tests.support.turn_rules_harness import end_round, pull, run, store

JOBS = load_prompts()["jobs"]
CAP = 65536


@pytest.fixture
def jobs(monkeypatch, tmp_path):
    """The module on this machine: its directory under tmp_path, a fake store, a round deadline the test
    sets (SimpleNamespace.deadline, UTC seconds or None), the jobs it marks, and a short floor for a late start."""
    state = SimpleNamespace(store=FakeStore(), deadline=None, marked=[], box=LocalBox())
    monkeypatch.setattr(command_jobs, "JOBS_DIR", str(tmp_path / "jobs"))
    monkeypatch.setattr(command_jobs, "store", lambda: state.store)
    monkeypatch.setattr(command_jobs, "round_deadline", lambda agent: state.deadline)
    monkeypatch.setattr(command_jobs, "mark_job", state.marked.append)
    monkeypatch.setattr(command_jobs, "LATE_START_S", 0.2)
    state.where = command_jobs.Where(box=state.box)
    return state


def command(jobs, text: str, agent: str = "a01", after=None):
    return command_jobs.run_command(jobs.where, agent, "shell", text, ["bash", "-c", text], after=after, cap=CAP)


def test_a_command_that_ends_in_its_round_answers_as_inspects_exec_did(jobs, tmp_path):
    """stderr, a newline, then stdout, the command's exit code; the seat's reap runs first, sparing no call; the call's
    directory is gone and no job is left. The exec is the launcher as root, whose tail is the tool's argv."""
    spared = []

    async def reap(keep):
        spared.append(keep)

    text = "echo out; echo err >&2; exit 3"
    outcome = asyncio.run(command(jobs, text, after=reap))
    direct = asyncio.run(jobs.box.exec(["bash", "-c", text]))
    assert outcome == command_jobs.Outcome(command_jobs.joined(direct.stderr, direct.stdout), returncode=3)
    assert outcome.text == "err\n\nout\n" and spared == [[]]
    (launch, user, _), (read, _, _) = jobs.box.calls[:2]
    assert user == "root" and command_jobs.command_argv(launch) == [*command_jobs.WRAP, "bash", "-c", text]
    assert read[:4] == ["python3", "-I", "-c", command_jobs.READ]
    assert not list((tmp_path / "jobs").iterdir()) and command_jobs.calls() == {}
    assert jobs.store == {} and jobs.marked == []


def test_a_call_whose_machine_goes_down_answers_with_the_execs_own_result(jobs, monkeypatch):
    """An agent as root kills its init's children and the container restarts: the read of the call's output finds no
    machine. The call answers with the exec's own result, as Inspect's exec did, and the sample goes on; a read that
    fails for a job still raises."""
    down = "Error response from daemon: Container 944e is restarting, wait until the container is running\n"
    real = jobs.box.exec

    async def restarting(cmd, *args, **kwargs):
        if list(cmd[:4]) == ["python3", "-I", "-c", command_jobs.READ]:
            jobs.box.calls.append((list(cmd), kwargs.get("user"), kwargs.get("cwd")))
            return ExecResult(success=False, returncode=1, stdout="", stderr=down)
        return await real(cmd, *args, **kwargs)

    monkeypatch.setattr(jobs.box, "exec", restarting)
    outcome = asyncio.run(command(jobs, "echo gone; exit 137"))
    assert outcome == command_jobs.Outcome("", returncode=137), outcome      # its output went to the call's pipes
    with pytest.raises(command_jobs.ReadFailed, match="restarting"):
        asyncio.run(command_jobs.read(command_jobs.Call(handle="a01-9", agent="a01", tool="shell", where=jobs.where,
                                                        command="true", started=0.0, task=None)))


def test_the_command_runs_under_the_wrap_as_the_root_of_the_exec(jobs, monkeypatch):
    """In a sandbox the command runs under Inspect's timeout with its limit disabled, so the root keeps the shape the
    logs know even when bash replaces itself with a lone command; here a forking wrapper stands in for it. The root's
    argv is command_argv's."""
    monkeypatch.setattr(command_jobs, "WRAP", ["/usr/bin/time", "-p"])
    outcome = asyncio.run(command(jobs, "ps -o args= -p $PPID; true"))
    launch = jobs.box.calls[0][0]
    assert command_jobs.command_argv(launch) == ["/usr/bin/time", "-p", "bash", "-c", "ps -o args= -p $PPID; true"]
    assert outcome.text.splitlines()[-1].strip() == "/usr/bin/time -p bash -c ps -o args= -p $PPID; true"
    monkeypatch.setattr(command_jobs, "WRAP", ["/no/such/wrapper"])          # a machine without it: the command bare
    assert asyncio.run(command(jobs, "echo bare")).text == "bare\n"


def test_the_launcher_replaces_itself_with_the_command(jobs, monkeypatch):
    """The exec's root process runs the tool's argv, so the access log and the network log hash and name it as before;
    nothing but the command writes its output. The command keeps the exec's umask, the call's own files 077."""
    monkeypatch.setattr(command_jobs, "WRAP", [])
    outcome = asyncio.run(command(jobs, "ps -o args= -p $$; true"))
    assert outcome.text.strip() == "bash -c ps -o args= -p $$; true"
    direct = asyncio.run(jobs.box.exec(["bash", "-c", "umask"])).stdout
    assert asyncio.run(command(jobs, "umask")).text == direct and direct.strip() != "0077"


def test_a_command_still_running_when_its_round_runs_out_becomes_a_job(jobs):
    """The call returns by the deadline with the job's id and its output so far; job_output then gives what is new and
    whether it still runs, and once it has exited its exit code; the record follows it. One event loop, as a run's."""
    async def scenario():
        jobs.deadline = time.time() + 1.0
        started = time.time()
        outcome = await command(jobs, "echo early; sleep 2.5; echo late >&2; echo done")
        assert 0.9 < time.time() - started < 2.0 and outcome.job == "j1" and outcome.returncode is None
        assert outcome.text == f"{JOBS['kept'].format(job='j1')} {JOBS['so_far']}\nearly\n"
        assert jobs.marked == ["j1"]
        record = jobs.store[rules.JOBS_STORE]["j1"]
        assert (record["agent"], record["tool"], record["status"], record["exit_code"], record["ended"]) == \
            ("a01", "shell", "running", None, None)
        assert record["command"] == "echo early; sleep 2.5; echo late >&2; echo done" and record["bytes"]["out"] == 6
        assert await command_jobs.job_output("a01", "j1", CAP) == f"{JOBS['running'].format(job='j1')} {JOBS['no_new']}"
        await until(command_jobs.owned("a01", "j1").task.done)
        assert await command_jobs.job_output("a01", "j1", CAP) == \
            f"{JOBS['exited'].format(job='j1', code=0)} {JOBS['new']}\nlate\n\ndone\n"
        record = jobs.store[rules.JOBS_STORE]["j1"]
        assert (record["status"], record["exit_code"], record["bytes"]) == ("exited", 0, {"out": 11, "err": 5})
        assert record["ended"] >= record["started"] + 2.5
        assert await command_jobs.job_output("a01", "j1", CAP) == \
            f"{JOBS['exited'].format(job='j1', code=0)} {JOBS['no_new']}"

    asyncio.run(scenario())


def test_a_jobs_recorded_end_follows_its_last_action(jobs, tmp_path):
    """A job's end is taken once its exec returned, so on one clock nothing the job did falls after it: here its last
    action stamps the time, then the job exits. The access log stamps opens on the machine's clock, so the smokes allow
    the two clocks' skew (round_time_scenario.CLOCK_SKEW_S)."""
    stamps = tmp_path / "stamps"

    async def scenario():
        for _ in range(5):
            jobs.deadline = time.time()
            stamp = f"python3 -c 'import time; open(\"{stamps}\", \"a\").write(repr(time.time()) + \"\\n\")'"
            outcome = await command(jobs, f"sleep 0.4; {stamp}; exit 3")
            assert outcome.job is not None
            await until(command_jobs.owned("a01", outcome.job).task.done)
            await command_jobs.job_output("a01", outcome.job, CAP)
        for number, last in enumerate(stamps.read_text().split(), 1):
            record = jobs.store[rules.JOBS_STORE][f"j{number}"]
            assert (record["status"], record["exit_code"]) == ("exited", 3) and record["ended"] >= float(last), \
                (record, last)

    asyncio.run(scenario())


def test_a_job_is_its_owners_and_job_stop_kills_it(jobs):
    async def scenario():
        jobs.deadline = time.time()
        outcome = await command(jobs, "sleep 30")
        assert outcome.job == "j1" and outcome.text.endswith(JOBS["nothing_yet"])
        with pytest.raises(ToolError, match="No such job: j1."):
            await command_jobs.job_output("a02", "j1", CAP)
        with pytest.raises(ToolError, match="No such job: j9."):
            await command_jobs.job_stop("a01", "j9", CAP)
        started = time.time()
        assert await command_jobs.job_stop("a01", "j1", CAP) == f"{JOBS['stopped'].format(job='j1')} {JOBS['no_new']}"
        assert time.time() - started < 10
        record = jobs.store[rules.JOBS_STORE]["j1"]
        assert record["status"] == "stopped" and record["exit_code"] not in (None, 0) and record["ended"] is not None

    asyncio.run(scenario())


def test_the_end_of_the_run_kills_every_job_still_running(jobs):
    """end_jobs: a job that exited stays exited, one still running is killed; every record is written in order, and a
    second call changes nothing."""
    async def scenario():
        jobs.deadline = time.time()
        first = await command(jobs, "sleep 0.5; echo ok")
        second = await command(jobs, "sleep 30", agent="a02")
        assert (first.job, second.job) == ("j1", "j2")
        await until(command_jobs.owned("a01", "j1").task.done)
        records = await command_jobs.end_jobs()
        assert [(r["id"], r["agent"], r["status"]) for r in records] == [("j1", "a01", "exited"),
                                                                          ("j2", "a02", "killed")]
        assert records[0]["exit_code"] == 0 and records[0]["bytes"]["out"] == 3 and records[1]["ended"] is not None
        assert id(jobs.store) not in command_jobs._SAMPLES and await command_jobs.end_jobs() == records

    asyncio.run(scenario())


def test_the_calls_of_a_reply_past_its_round_time_share_one_late_wait(jobs, monkeypatch):
    """The calls of one reply run one after another. Once the round's time has passed, the first call to start waits
    LATE_START_S and every later call of that round waits only what is left of that wait, so k long calls end the
    round at T plus one wait, not k waits. A quick command inside the wait still answers whole; the next round's
    deadline gets a wait of its own."""
    monkeypatch.setattr(command_jobs, "LATE_START_S", 1.0)

    async def scenario():
        jobs.deadline = time.time() - 0.5
        started = time.time()
        quick = await command(jobs, "echo quick")
        assert quick.job is None and quick.text == "quick\n"
        kept = [await command(jobs, "sleep 30") for _ in range(4)]
        took = time.time() - started
        assert [o.job for o in kept] == ["j1", "j2", "j3", "j4"]
        assert 0.9 < took < 1.8, took                       # one wait of 1 s; four waits would be 4 s
        jobs.deadline = time.time() - 0.1                   # the next round, also past its time
        started = time.time()
        assert (await command(jobs, "sleep 30")).job == "j5"
        assert 0.9 < time.time() - started < 1.8
        await command_jobs.end_jobs()

    asyncio.run(scenario())


def leftovers(token: str) -> list[int]:
    """The pids of this machine's processes whose command line holds the test's own random token (a job's collector
    too, while it runs: its argv holds the command)."""
    out = subprocess.run(["pgrep", "-f", token], capture_output=True, text=True).stdout
    return [int(pid) for pid in out.split()]


WAIT_S = 30.0
"""The longest a test waits for a condition. Each wait ends as soon as its condition holds, so a loaded machine only
makes the test slower, never wrong; a condition that never holds fails the assertion after it."""


async def until(holds: Callable[[], bool]) -> None:
    """Wait until holds() is true, or WAIT_S passed."""
    end = time.monotonic() + WAIT_S
    while not holds() and time.monotonic() < end:
        await asyncio.sleep(0.05)


def kill_leftovers(*tokens: str) -> None:
    """The test's cleanup: SIGKILL what is left of its tokens. A process listed may end before its kill (a collector at
    its last look, a process the reap killed still dying), which is what the cleanup wants."""
    for pid in [pid for token in tokens for pid in leftovers(token)]:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


async def settled(token: str, keep: int = 0) -> list[int]:
    """leftovers(token) once at most `keep` are left, or after WAIT_S: a job's collector ends at its next look, and a
    process the stop missed stays."""
    await until(lambda: len(leftovers(token)) <= keep)
    return leftovers(token)


def background(token: str, hold: Path | None = None) -> str:
    """A command that leaves four processes behind in four ways and then exits: a background subshell, nohup, a
    process in a session of its own and disown. The collector finds the one in a session of its own through its
    parent, the command, so the command waits until the collector has recorded it in the call's `procs` before it
    exits, however late the collector's look comes. With `hold` it also waits until that file exists, so the test
    decides when it exits. Each wait lasts WAIT_S at most."""
    procs = f'"{command_jobs.JOBS_DIR}"/*/procs'
    tries = int(WAIT_S / 0.05)
    held = f'for _ in $(seq {tries}); do [ -e "{hold}" ] && break; sleep 0.05; done; ' if hold else ""
    return (f"( sleep {token}1 ) & nohup sleep {token}2 >/dev/null 2>&1 & "
            f"python3 -c 'import os, time; os.setsid(); time.sleep({token}3)' >/dev/null 2>&1 & own=$!; "
            f"sleep {token}4 & disown; "
            f"for _ in $(seq {tries}); do grep -qs \"\\\"$own\\\"\" {procs} && break; sleep 0.05; done; "
            f"{held}echo left")


def test_what_a_job_left_running_dies_at_job_stop_and_at_the_end_of_the_run(jobs, tmp_path):
    """A job that starts background processes and exits leaves them running; job_stop kills them and the job stays
    exited, and the end of the run kills another job's. A process that is no job's (another agent's, the harness's)
    and another agent's job still running are untouched."""
    first, second, other = (str(7000 + secrets.randbelow(2000)) + str(i) for i in range(3))
    outsider = subprocess.Popen(["sleep", f"{other}9"], start_new_session=True)

    async def scenario():
        jobs.deadline = time.time()
        go = tmp_path / "go"
        a = await command(jobs, background(first, hold=go))
        b = await command(jobs, background(second, hold=go), agent="a02")
        c = await command(jobs, f"sleep {other}8", agent="a03")
        assert (a.job, b.job, c.job) == ("j1", "j2", "j3")
        go.touch()
        ab = [command_jobs.owned("a01", "j1"), command_jobs.owned("a02", "j2")]
        await until(lambda: all(j.task.done() for j in ab) and len(leftovers(first)) >= 4
                    and len(leftovers(second)) >= 4)
        assert len(leftovers(first)) >= 4 and len(leftovers(second)) >= 4      # the subshell may stay a bash of its own
        answer = await command_jobs.job_stop("a01", "j1", CAP)
        assert answer.startswith(JOBS["exited"].format(job="j1", code=0))
        assert await settled(first) == [] and len(leftovers(second)) >= 4
        assert outsider.pid in leftovers(other) and len(leftovers(other)) >= 2      # a03's job runs on
        records = await command_jobs.end_jobs()
        assert [r["status"] for r in records] == ["exited", "exited", "killed"]
        assert await settled(second) == [] and await settled(other, keep=1) == [outsider.pid]

    try:
        asyncio.run(scenario())
    finally:
        kill_leftovers(first, second, other)


def test_the_rounds_reap_kills_what_its_calls_left_and_spares_its_running_job(jobs):
    """reap_round at the end of a01's round: what a call that ended in time left running dies, a01's job still
    running and what it started live on, a02's leftovers are not a01's, and a call reaped once is not reaped again."""
    done, kept, other = (str(7000 + secrets.randbelow(2000)) + str(i) for i in range(3))
    jobs.where = command_jobs.Where(box=jobs.box, tracked=True)

    async def scenario():
        quick = await command(jobs, background(done))
        assert sorted(os.listdir(f"{command_jobs.JOBS_DIR}/a01-1")) == ["pid", "procs"]      # its output is gone
        assert quick.job is None and quick.text.strip() == "left"
        await command(jobs, background(other), agent="a02")
        jobs.deadline = time.time()
        long = await command(jobs, f"sleep {kept}7 & sleep {kept}8")
        assert long.job == "j1"
        await until(lambda: len(leftovers(done)) >= 4 and len(leftovers(kept)) >= 2)
        assert len(leftovers(done)) >= 4 and len(leftovers(kept)) >= 2
        first = await command_jobs.reap_round("a01", jobs.box, None)
        assert first["calls"] == 1                    # its group dies at once; killed names the members left after it
        assert await settled(done) == [] and len(leftovers(kept)) >= 2 and len(leftovers(other)) >= 4
        assert not os.path.exists(f"{command_jobs.JOBS_DIR}/a01-1")
        assert (await command_jobs.reap_round("a01", jobs.box, None))["calls"] == 0     # once, and the job runs on
        assert (await command_jobs.reap_round("a01", jobs.box, "elsewhere"))["calls"] == 0   # another machine's
        await command_jobs.end_jobs()

    try:
        asyncio.run(scenario())
    finally:
        kill_leftovers(done, kept, other)


def test_a_sealed_call_keeps_no_output_on_its_machine(jobs, tmp_path):
    """peer_shell runs sealed (Where.sealed): its key reaches the launcher on the exec's stdin, never on a command
    line, and the collector writes each stream XORed with a keystream of it, so no file of the call's folder holds
    the output while the job runs or after it exited, where a root agent of that machine could read it. The answers
    are the plain output, as an unsealed call's; the command's stdin is empty, as with no input."""
    sealed = command_jobs.Where(box=jobs.box, sealed=True)

    def run(text):
        return command_jobs.run_command(sealed, "a01", "peer_shell", text, ["bash", "-c", text], cap=CAP)

    def on_disk() -> bytes:
        return b"".join(p.read_bytes() for p in (tmp_path / "jobs").rglob("*") if p.is_file())

    async def scenario():
        jobs.deadline = time.time() + 1.0
        kept = await run("echo SEALMARK-out; echo SEALMARK-err >&2; wc -c | tr -d ' '; sleep 2; echo SEALMARK-late")
        assert kept.job == "j1" and kept.text.endswith(f"{JOBS['so_far']}\nSEALMARK-err\n\nSEALMARK-out\n0\n"), kept
        assert b"SEALMARK" not in on_disk() and len(on_disk()) > 0
        await until(command_jobs.owned("a01", "j1").task.done)
        assert b"SEALMARK" not in on_disk()
        assert await command_jobs.job_output("a01", "j1", CAP) == \
            f"{JOBS['exited'].format(job='j1', code=0)} {JOBS['new']}\nSEALMARK-late\n"
        jobs.deadline = time.time() + 10
        quick = await run("printf 'SEALMARK-quick \\303\\251\\n'; exit 4")
        assert quick == command_jobs.Outcome("SEALMARK-quick \u00e9\n", returncode=4), quick
        launches = [argv for argv, _, _ in jobs.box.calls if argv[:4] == ["python3", "-I", "-c", command_jobs.LAUNCH]]
        assert len(launches) == 2 and all(json.loads(a[4])["sealed"] is True for a in launches)
        await command_jobs.end_jobs()

    asyncio.run(scenario())


def test_each_call_folder_is_named_by_its_agent_and_its_number(jobs, tmp_path):
    """A call's folder under JOBS_DIR is <agent>-<n>, the agent's n-th command call of the run (call_name), so a
    command that lists the folder reads the same names in the run and in its replay, and two calls on one machine
    never race for a name; calls of one batch started at once take one number each."""
    async def scenario():
        jobs.deadline = time.time()
        kept = await command(jobs, "sleep 30")
        listed = await command(jobs, f"ls {tmp_path / 'jobs'}", agent="a02")
        assert listed.text == "a01-1\na02-1\n"           # the a01 job's folder and the listing call's own
        jobs.deadline = time.time() + 0.5
        pair = await asyncio.gather(command(jobs, "sleep 5"), command(jobs, "sleep 5"))
        assert kept.job == "j1" and sorted(o.job for o in pair) == ["j2", "j3"]
        assert sorted(p.name for p in (tmp_path / "jobs").iterdir()) == ["a01-1", "a01-2", "a01-3"]
        assert command_jobs.call_name("a01", 2) == "a01-2" and command_jobs.call_name("x/../y", 1) == "x_.._y-1"
        await command_jobs.end_jobs()

    asyncio.run(scenario())


def test_a_batch_kept_at_once_takes_one_id_each(jobs):
    async def scenario():
        jobs.deadline = time.time() + 0.5
        outcomes = await asyncio.gather(command(jobs, "sleep 5"), command(jobs, "sleep 5"))
        assert sorted(o.job for o in outcomes) == ["j1", "j2"]
        assert sorted(jobs.store[rules.JOBS_STORE]) == ["j1", "j2"]
        await command_jobs.end_jobs()

    asyncio.run(scenario())


def test_a_stream_keeps_its_last_bytes(jobs, monkeypatch):
    """Inspect's exec keeps the last bytes of a stream past its limit; so does the collector, and it counts them
    all."""
    monkeypatch.setattr(command_jobs, "KEEP_BYTES", 1000)
    text = "python3 -c \"import sys; sys.stdout.write(''.join(f'{i:05d}' for i in range(1000)))\""
    full = "".join(f"{i:05d}" for i in range(1000))

    async def scenario():
        outcome = await command(jobs, text)
        assert outcome.text == full[-1000:]
        jobs.deadline = time.time() + 1.0
        kept = await command(jobs, f"{text}; sleep 5")
        assert jobs.store[rules.JOBS_STORE][kept.job]["bytes"]["out"] == 5000 and kept.text.endswith(full[-1000:])
        await command_jobs.end_jobs()

    asyncio.run(scenario())


def test_an_answer_fits_the_cap(jobs):
    async def scenario():
        jobs.deadline = time.time() + 1.0
        outcome = await command_jobs.run_command(jobs.where, "a01", "shell", "x", [
            "bash", "-c", "head -c 300000 /dev/zero | tr '\\0' x; sleep 5"], cap=2048)
        assert len(outcome.text.encode()) <= 2048 and "[cut:" in outcome.text and outcome.job == "j1"
        await command_jobs.end_jobs()

    asyncio.run(scenario())


def test_the_job_windows_and_the_run_json_block():
    jobs = {"j2": {"id": "j2", "call_id": "c2", "ended": None}, "j1": {"id": "j1", "call_id": "c1", "ended": 5.0},
            "j10": {"id": "j10", "call_id": None, "ended": 9.0}}
    assert command_jobs.job_windows(jobs) == {"c1": 5.0, "c2": None}
    view = SimpleNamespace(store_get=lambda key: jobs if key == rules.JOBS_STORE else None)
    assert [j["id"] for j in blocks.jobs_block(view)] == ["j1", "j2", "j10"]
    assert blocks.jobs_block(SimpleNamespace(store_get=lambda key: None)) is None      # a run before the jobs


# ------------------------------------------------------------------------------------------------ the tools around them

def test_no_command_tool_keeps_a_time_limit():
    assert agent_tools.lone_tool_kwargs("user") == {"bash": {"user": "user"}, "python": {"user": "user"}}
    tools = agent_tools.roster_tools(["a01"], None)
    assert tools.tool_kwargs == agent_tools.lone_tool_kwargs(agent_tools.AGENT_NAME)
    assert not hasattr(tools, "timeout")
    assert agent_tools.holds_commands(["shell"]) and agent_tools.holds_commands(["peer_shell"])
    assert not agent_tools.holds_commands(["board_read", "get_board"])


def test_a_lone_agents_bash_and_python_run_through_the_jobs(monkeypatch):
    """ORBIT binds bash and python under names no binding may take; the gate swaps their executor and keeps their
    name, description and parameters."""
    ran = []

    async def fake_run(seat, agent, text, **kwargs):
        ran.append((seat, agent, text, kwargs))
        return "ok"

    monkeypatch.setattr(agent_tools, "run", fake_run)
    assert rules.SWAPS == {"bash": agent_tools.lone_swap, "python": agent_tools.lone_swap}
    for tool, argument in ((bash(user="user"), {"command": "ls"}), (python(user="user"), {"code": "print(1)"})):
        before = ToolDef(tool)
        after = agent_tools.lone_swap("a01", before)
        assert (after.name, after.description, after.parameters) == (before.name, before.description,
                                                                      before.parameters)
        assert asyncio.run(after.tool(**argument)) == "ok"
    assert ran == [(agent_tools.LONE_SEAT, "a01", "ls", {"tool": "bash"}),
                   (agent_tools.LONE_SEAT, "a01", "python3 -", {"tool": "python", "input": "print(1)",
                                                                "shown": "print(1)"})]
    assert agent_tools.LONE_SEAT == agent_tools.Seat(user=agent_tools.AGENT_NAME) and not agent_tools.LONE_SEAT.reap


def test_a_peer_shell_job_answers_in_json():
    answer = peer.answer("a02", "sleep 9", None, "Still running as job j3", 65536, job="j3")
    assert list(answer) == ["peer", "command", "job", "exit_code", "output", "truncated"]
    assert (answer["job"], answer["exit_code"]) == ("j3", None)
    assert "job" not in peer.answer("a02", "ls", 0, "", 65536)


# ------------------------------------------------------------------------------------------------ the round's clock

def clocked(monkeypatch, *, round_time_s, t0, made=1, calls_per_round=10):
    """_turn_rounds on a fake store and Live: one round of the agent under way since t0."""
    live = Live()
    live.rounds["a01"] = {"round": 1, "n": 1, "t0": t0, "t1": None, "made": made, "closed": False, "end": None,
                          "runs": [], "times": {}, "seen": None}
    st = SimpleNamespace(round_time_s=round_time_s, time_limit_s=None, started_at=None, ended={},
                         calls_per_round=calls_per_round, rounds_used={}, call_budget=0, calls_used={})
    monkeypatch.setattr(_turn_rounds, "live_record", lambda: live)
    monkeypatch.setattr(_turn_rounds, "current_round", lambda: 1)
    monkeypatch.setattr(_turn_rounds, "store_as", lambda model: st if model is rules.TurnState
                        else SimpleNamespace(agent=None))
    return live


def test_a_round_ends_once_its_time_has_passed(monkeypatch):
    boundary = SimpleNamespace(after="tools", messages=[])
    clocked(monkeypatch, round_time_s=600, t0=time.time() - 601)
    assert _turn_rounds.ends("a01", boundary) == "round_time" and _turn_rounds.round_deadline("a01") < time.time()
    clocked(monkeypatch, round_time_s=600, t0=time.time() - 10)
    assert _turn_rounds.ends("a01", boundary) is None
    assert abs(_turn_rounds.round_deadline("a01") - (time.time() + 590)) < 2
    clocked(monkeypatch, round_time_s=0, t0=time.time() - 10_000)          # no round clock
    assert _turn_rounds.ends("a01", boundary) is None and _turn_rounds.round_deadline("a01") is None
    clocked(monkeypatch, round_time_s=600, t0=time.time() - 601, made=10)   # N reached first: calls
    assert _turn_rounds.ends("a01", boundary) == "calls"
    assert "round_time" in _turn_rounds.END_REASONS


def test_a_round_records_how_far_it_ran_over_and_the_job_of_each_call():
    assert turn_activation.over_time({"t0": 100.0, "t1": 712.5}, 600) == 12.5
    assert turn_activation.over_time({"t0": 100.0, "t1": 650.0}, 600) is None
    assert turn_activation.over_time({"t0": 100.0, "t1": 9000.0}, 0) is None
    assert turn_activation.over_time(None, 600) is None


def test_mark_job_names_the_call_under_way():
    run_record = {"tool": "shell"}
    token = _turn_rounds.RUN.set(run_record)
    try:
        rules.mark_job("j4")
    finally:
        _turn_rounds.RUN.reset(token)
    assert run_record["job"] == "j4"
    rules.mark_job("j5")                                  # outside a call: nothing to name


# ------------------------------------------------------------------------------------------------ a whole run

def jobs_task(config, box):
    """One agent with a shell on this machine (command_jobs, `bash -c`), the job tools and end_round, under the turn
    rules; its scorer ends the jobs."""
    def shell(ctx):
        async def execute(command: str) -> str:
            """Run a command.

            Args:
                command: The command.
            """
            outcome = await command_jobs.run_command(command_jobs.Where(box=box), ctx.agent_name, "shell", command,
                                                     ["bash", "-c", command], cap=CAP)
            return outcome.text
        return ToolDef(execute, name="shell")

    agent = AgentSpec(name="a01", role="engineer", tools=["shell", *command_jobs.TOOLS, rules.END_ROUND],
                      max_messages=10_000, submit=False, system_prompt="You are a01, an engineer.")
    bindings = capped_bindings({"shell": ToolFactory(shell), **command_jobs.job_bindings(cap=lambda a: CAP),
                                **end_round_bindings()}, config)
    task = multi_agent_task(name="jobs_test", setup=SetupConfig(agents=[agent]), tool_bindings=bindings,
                            execution=board_execution(plan=rules.schedule(config, ["a01"]),
                                                      initial_context="sample_input"),
                            scheduler=board_scheduler(max_turns=config.max_turns + 5, max_time_seconds=120),
                            dataset=[Sample(id="jobs", input="Work.")], scorer=command_jobs.jobs_capture(),
                            task_setup=[rules.rules_setup(config, ["a01"])], token_limit=1_000_000)
    for sample in task.dataset:
        sample.metadata["experiment"]["communication"] = None
        sample.metadata["experiment"]["defenses"] = [rules.turn_rules_defense(config).model_dump(mode="json")]
        ExperimentConfig.model_validate(sample.metadata["experiment"])
    return task


def test_a_whole_run_keeps_a_call_as_a_job_and_ends_each_round_on_time(monkeypatch, tmp_path):
    """Round 1: a command outlives the round's two seconds, becomes j1 and the round ends round_time. Round 2: j1
    still runs; a second command becomes j2. Round 3: j1 has exited with its last output. The run's end kills j2. The
    timeline names each call's job and the record its call."""
    monkeypatch.setattr(command_jobs, "JOBS_DIR", str(tmp_path / "jobs"))
    monkeypatch.setattr(command_jobs, "LATE_START_S", 0.2)
    box = LocalBox()
    monkeypatch.setattr(harness, "build_task", lambda config, **_: jobs_task(config, box))
    shell = lambda text: calls(("shell", {"command": text}))       # noqa: E731
    read = calls(("job_output", {"job": "j1"}))
    script = [shell("echo early; sleep 3.5; echo late"), read, shell("sleep 60"), read, end_round()]
    config = pull(n_agents=1, rounds=3, calls_per_round=3, round_time_s=2)
    sample, _ = run(config, {"a01": script}, tmp_path)
    timeline = store(sample, "timeline")
    # then waits until the nine calls are made: a wait uses one
    assert [e["end"] for e in timeline] == ["round_time", "round_time", *["end_round"] * 5]
    assert [c.get("job") for c in timeline[0]["calls"]] == ["j1"]
    assert [c.get("job") for c in timeline[1]["calls"]] == [None, "j2"]
    assert all(e["t1"] - e["t0"] < 2 + 3 for e in timeline[:2])                # within T and a model call
    results = [(e.function, str(e.result)) for e in sample.events if e.event == "tool"]
    assert results[0][1] == f"{JOBS['kept'].format(job='j1')} {JOBS['so_far']}\nearly\n"
    assert results[1][1] == f"{JOBS['running'].format(job='j1')} {JOBS['no_new']}"
    assert results[3][1] == f"{JOBS['exited'].format(job='j1', code=0)} {JOBS['new']}\nlate\n"
    records = sample.store[rules.JOBS_STORE]
    assert (records["j1"]["status"], records["j1"]["exit_code"]) == ("exited", 0)
    assert records["j2"]["status"] == "killed"
    assert records["j1"]["call_id"] == timeline[0]["calls"][0]["id"]
    assert records["j2"]["call_id"] == timeline[1]["calls"][1]["id"]
