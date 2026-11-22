"""Command calls with no time limit of their own and the jobs they become (src/sandbox/command_jobs.py), run for real on
this machine (tests/support/local_box.py): a command that ends in its round answers as Inspect's exec did, one still
running when the round's time runs out becomes a job that the agent reads and stops in later rounds, the jobs still
running at the end of the run are killed, and the round ends on time (turn_rules round_time). No Docker, no model call.
"""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest
from inspect_ai.dataset import Sample
from inspect_ai.tool import ToolDef, ToolError
from inspect_ai.tool._tools._execute import bash, python
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
    assert user == "root" and command_jobs.command_argv(launch) == ["bash", "-c", text]
    assert read[:4] == ["python3", "-I", "-c", command_jobs.READ]
    assert not list((tmp_path / "jobs").iterdir()) and command_jobs.calls() == {}
    assert jobs.store == {} and jobs.marked == []


def test_the_launcher_replaces_itself_with_the_command(jobs):
    """The exec's root process runs the tool's argv, so the access log and the network log hash and name it as before;
    nothing but the command writes its output. The command keeps the exec's umask, the call's own files 077."""
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
        await asyncio.sleep(2.0)
        assert await command_jobs.job_output("a01", "j1", CAP) == \
            f"{JOBS['exited'].format(job='j1', code=0)} {JOBS['new']}\nlate\n\ndone\n"
        record = jobs.store[rules.JOBS_STORE]["j1"]
        assert (record["status"], record["exit_code"], record["bytes"]) == ("exited", 0, {"out": 11, "err": 5})
        assert record["ended"] >= record["started"] + 2.5
        assert await command_jobs.job_output("a01", "j1", CAP) == \
            f"{JOBS['exited'].format(job='j1', code=0)} {JOBS['no_new']}"

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
        await asyncio.sleep(1.0)
        records = await command_jobs.end_jobs()
        assert [(r["id"], r["agent"], r["status"]) for r in records] == [("j1", "a01", "exited"),
                                                                          ("j2", "a02", "killed")]
        assert records[0]["exit_code"] == 0 and records[0]["bytes"]["out"] == 3 and records[1]["ended"] is not None
        assert id(jobs.store) not in command_jobs._SAMPLES and await command_jobs.end_jobs() == records

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
                         calls_per_round=calls_per_round, rounds_used={})
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
    assert [e["end"] for e in timeline] == ["round_time", "round_time", "end_round"]
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
