"""The command calls of a run made since the round clock (src/sandbox/command_jobs.py), played back as the run played
them: every command through command_jobs' own launcher with no time limit of its own, a call that became a job kept
running as that job, its job_output reads and its job_stop, and peer_shell's commands on the peer's machine as the
influencer's observer, on the run's clock.

    jobs = JobPlayer.of(run, calls, service_of=lambda agent: agent)     # None for a run before the round clock
    output, timed_out = jobs.execute(box, call, replayer)               # replayer.place(call) says where it ran
    jobs.end(box)                                                       # what still runs is killed, as end_jobs did
    report.jobs = jobs.report(played)                                   # {"jobs": [...], "problems": [...]}

What the record gives. run.json "jobs" names each call that became a job (call_id), its id, its end and its status
(exited, stopped, killed) with its exit code; the eval log gives each call's start and end; run.json "peer_shell" each
influencer's observer uid; the sample store each peer's working directory and home (peer_shell.PLACES).

The clock. A job keeps running between calls, so where its writes land among the calls depends on time. While any
call or job is running, each call starts at its recorded start plus a fixed offset (`offset`, the replay's clock minus
the run's), so a job has run as long at each call as it had in the run. While nothing runs, the replay skips ahead:
the offset shrinks to the next call's start. replay_run.play_paced keeps the clock.

A call that became a job waits until its recorded end on the replay's clock, as the run's call waited out its round,
then becomes the job and answers its output so far (prompts.yaml jobs.kept). A call that ended in time waits for its
command as long as it ran in the run and as long again, at least OVERRUN_S more; one still running then is stopped.
A job_output or job_stop on a job the run saw end first waits up to JOB_GRACE_S for the replayed job to end.

Unfaithful. Each of these is a problem, and a replay with any problem is not faithful (replay_run.replay): a call the
run kept as a job that ended in time in the replay; a call that ended in time in the run and ran past its bound in the
replay; a job whose replayed status or exit code differs from its record; a recorded job the replay never started; a
job call (the call that became the job, job_output, job_stop, peer_shell) whose output differs from the record.

Not reproduced. The replay's machines do not hide /proc (src/sandbox/proc_hiding/), so a command that reads another
process's /proc differs in a run that hid it. A commit a job makes is dated in the window of the job call that follows
it (window), or of the next call of its agent; one made after its last job call stays undated. Each command's directory
is named as the run named it (command_jobs.call_name: its owner and its number among the owner's command calls), so a
command that lists them reads the same names.
"""
from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import NamedTuple

from src.configuration.settings import load_prompts
from src.measures.run_directory import RunDir
from src.measures.shell_commands import TOOLS
from src.replay.recorded_calls import RecordedCall, ReplayRefused, sample_store
from src.replay.replay_sandbox import Exec
from src.sandbox import command_jobs as cj
from src.sandbox import peer_shell as ps
from src.sandbox.proc_hiding.proc_hiding import PROC_GID
from src.swarm.tool_output_caps import DEFAULT_CAP, NO_CAP

JOB_TOOLS = cj.TOOLS
PEER_SHELL = ps.TOOL
OVERRUN_S = 60.0
"""The least a call that ended in time in the run may run past its recorded duration in the replay."""
JOB_GRACE_S = 10.0
"""How long a job call waits for a replayed job that the run saw end before the call."""
POLL_S = 0.05


def jobs_era(run_json: dict) -> bool:
    """Whether the run ran its commands through command_jobs: its config has round_time_s (since dc4ea6d)."""
    return "round_time_s" in (run_json.get("config") or {})


def cap_of(config: dict) -> int:
    """The bytes of one answer an agent saw whole, as tool_output_caps.cap_of read the run's config."""
    cap = config.get("tool_output_bytes")
    cap = DEFAULT_CAP if cap is None else cap
    return cap if cap > 0 else NO_CAP


class Place(NamedTuple):
    """Where and how a command ran (command_jobs.Where and its argv): the compose service, the user LAUNCH takes (None
    for root), the working directory (None for the image's), the environment it adds, argv, stdin, and the seat's reap
    after a command that ended in time, called with (box, pid files to spare), or None."""
    service: str
    user: str | None
    cwd: str | None
    env: dict
    argv: list[str]
    stdin: bytes | None = None
    reap: Callable | None = None


@dataclass
class Live:
    """One replayed command: its exec runs in a thread (ReplaySandbox.run with no timeout) until the command ends."""
    handle: str
    owner: str
    service: str
    started: float
    top: str
    thread: threading.Thread | None = None
    result: dict = field(default_factory=dict)
    job: str | None = None
    status: str = "running"
    ended: float | None = None
    cursor: dict = field(default_factory=lambda: {"out": 0, "err": 0})

    @property
    def dir(self) -> str:
        return f"{self.top}/{self.handle}"

    @property
    def pid_file(self) -> str:
        return f"{self.dir}/pid"

    @property
    def done(self) -> bool:
        return self.thread is not None and not self.thread.is_alive()

    @property
    def exec(self) -> Exec | None:
        return self.result.get("exec")

    @property
    def exit_code(self) -> int | None:
        return self.exec.rc if self.done and self.exec is not None else None


class JobPlayer:
    def __init__(self, run_json: dict, calls: list[RecordedCall], *, service_of: Callable[[str], str],
                 places: dict | None = None, top: str = cj.JOBS_DIR) -> None:
        records = list((run_json.get("jobs") or {}).values()) if isinstance(run_json.get("jobs"), dict) \
            else list(run_json.get("jobs") or [])
        self.records = {r["id"]: r for r in records}
        self.kept_by_call = {r["call_id"]: r for r in records if r.get("call_id")}
        self.observers = {k: int(v) for k, v in ((run_json.get("peer_shell") or {}).get("observers") or {}).items()}
        self.places = places or {}
        self.service_of, self.top = service_of, top
        self.cap = cap_of(run_json.get("config") or {})
        self.observer_gid = PROC_GID if (run_json.get("config") or {}).get("proc_hiding") else None
        self.calls = {c.n: c for c in calls}
        self.offset = 0.0
        self.lock = threading.Lock()
        self.live: dict[str, Live] = {}          # recorded job id -> the replayed job
        self.inflight: dict[str, Live] = {}      # handle -> a command still running inside its call
        self.launched: dict[int, threading.Event] = {}
        self.numbers: dict[str, int] = {}        # each owner's command calls so far, for command_jobs.call_name
        self.problems: list[dict] = []
        self.texts = load_prompts()["jobs"]

    @classmethod
    def of(cls, run: RunDir, calls: list[RecordedCall], *, service_of: Callable[[str], str],
           top: str = cj.JOBS_DIR) -> JobPlayer | None:
        """The player of a run made since the round clock, else None (its commands replay under the old limit). A run
        with peer_shell calls needs the observers and the peers' places, else ReplayRefused."""
        if not jobs_era(run.run_json):
            return None
        places = None
        if any(c.tool == PEER_SHELL for c in calls):
            places = sample_store(run.eval_log).get(ps.PLACES) if run.eval_log else None
            if not places or not (run.run_json.get("peer_shell") or {}).get("observers"):
                raise ReplayRefused(f"{run.path.name}: peer_shell calls without the observers or the peers' places")
        return cls(run.run_json, calls, service_of=service_of, places=places, top=top)

    # ---------------------------------------------------------------- what the player plays
    def handles(self, call: RecordedCall) -> bool:
        """The calls the player adds to a replayer's sandbox calls: job_output, job_stop and peer_shell."""
        return call.error != "parsing" and (call.tool in JOB_TOOLS or call.tool == PEER_SHELL)

    def kept(self, call: RecordedCall) -> bool:
        """Whether the run kept the call as a job."""
        return call.id in self.kept_by_call

    def out_of_the_way(self, call: RecordedCall) -> bool:
        """Whether a call no longer holds back a call that runs alone: a kept call once its command started."""
        event = self.launched.get(call.n)
        return event is not None and event.is_set()

    def running(self) -> bool:
        """Whether any replayed command still runs: a job or a call's own command."""
        with self.lock:
            return any(not live.done for live in (*self.live.values(), *self.inflight.values()))

    def machine(self, call: RecordedCall) -> str | None:
        """The service a job call or a peer_shell call runs in; None for any other call."""
        if call.tool == PEER_SHELL:
            peer = str(call.arguments.get("peer") or "")
            return self.service_of(peer) if peer in self.places else None
        if call.tool in JOB_TOOLS:
            live = self.live.get(str(call.arguments.get("job") or ""))
            return live.service if live is not None else None
        return None

    def window(self, call: RecordedCall) -> RecordedCall:
        """The call with its window widened back to its job's start, for dating the commits the job made."""
        record = self.records.get(str(call.arguments.get("job") or "")) if call.tool in JOB_TOOLS else None
        if record and record.get("started"):
            return call._replace(t0=min(call.t0, float(record["started"])))
        return call

    def execute(self, box, call: RecordedCall, replayer) -> tuple[str, bool]:
        """The call as the run ran it: a command tool's in replayer.place(call), a job tool's on its job, peer_shell's
        on the peer. (output, timed out), timed out only for a call stopped past its bound."""
        if call.tool in JOB_TOOLS:
            return self.job_call(box, call), False
        if call.tool == PEER_SHELL:
            return self.peer_shell(box, call)
        if call.tool not in TOOLS:
            raise ValueError(f"the job player does not play {call.tool}")
        text, timed_out, _, _ = self.command(box, call, replayer.place(call))
        return text, timed_out

    # ---------------------------------------------------------------- the scripts
    def script(self, box, service: str, script: str, spec: dict) -> Exec:
        return box.run(service, ["python3", "-I", "-c", script], stdin=json.dumps(spec).encode(),
                       timeout=cj.INFRA_TIMEOUT_S, user="root")

    def read(self, box, live: Live, *, wait_s: float = 0.0, final: bool = False) -> tuple[str, str, dict]:
        """command_jobs.read on the replayed command: (stderr, stdout, head) from its cursor, which moves."""
        spec = {"dir": live.dir, "keep": cj.KEEP_BYTES, "wait_s": wait_s, "data": True, "remove": final,
                "from": {"out": 0, "err": 0} if final else live.cursor}
        done = self.script(box, live.service, cj.READ, spec)
        out, _, line = done.stdout.decode(errors="replace").rstrip("\n").rpartition("\n")
        try:
            head = json.loads(line)
        except ValueError:
            raise RuntimeError(f"replay jobs: the read failed: {done.stderr.decode(errors='replace')[-500:]}") \
                from None
        for name in ("out", "err"):
            if name in head:
                live.cursor[name] = head[name]["total"]
        return done.stderr.decode(errors="replace"), out, head

    def stop(self, box, live: Live, status: str) -> None:
        """command_jobs.stop: kill the command's session, wait for its exec, and name who stopped it."""
        if not live.done:
            self.script(box, live.service, cj.STOP, {"pid_files": [live.pid_file]})
            live.thread.join(cj.STOP_WAIT_S)
            live.status = status
            live.ended = live.ended or time.time()
        self.finished(live)

    @staticmethod
    def finished(live: Live) -> None:
        if live.done and live.status == "running":
            live.status = "exited"

    def keep(self, service: str, but: str) -> list[str]:
        """The pid files of the commands still running on the service, `but` left out: what a reap there spares."""
        with self.lock:
            return [live.pid_file for live in (*self.live.values(), *self.inflight.values())
                    if live.service == service and live.handle != but and not live.done]

    def problem(self, call: RecordedCall | None, why: str, **more) -> None:
        with self.lock:
            self.problems.append({**({"n": call.n, "agent": call.agent, "k": call.k, "call_id": call.id}
                                     if call else {}), "why": why, **more})

    # ---------------------------------------------------------------- the calls
    def command(self, box, call: RecordedCall, place: Place, owner: str | None = None
                ) -> tuple[str, bool, int | None, str | None]:
        """command_jobs.run_command on the replay's clock: (text, timed out, return code, job id)."""
        with self.lock:
            name = owner or call.agent
            self.numbers[name] = self.numbers.get(name, 0) + 1
            handle = cj.call_name(name, self.numbers[name])
        spec = {"top": self.top, "name": handle, "user": place.user, "env": place.env, "keep": cj.KEEP_BYTES,
                "wrap": cj.WRAP}
        live = Live(handle=handle, owner=owner or call.agent, service=place.service, started=time.time(),
                    top=self.top)

        def run() -> None:
            live.result["exec"] = box.run(place.service, cj.launch_argv(spec, place.argv), stdin=place.stdin,
                                          timeout=None, workdir=place.cwd, user="root")
            live.ended = time.time()
        live.thread = threading.Thread(target=run, daemon=True, name=f"replay-job-{handle}")
        record = self.kept_by_call.get(call.id)
        with self.lock:
            self.inflight[handle] = live
            event = self.launched.setdefault(call.n, threading.Event())
        live.thread.start()
        if record is not None:
            event.set()
            wait = call.t1 + self.offset - time.time()
        else:
            ran = max(0.0, call.t1 - call.t0)
            wait = ran + max(OVERRUN_S, ran)
        live.thread.join(max(0.0, wait))
        if live.done:
            with self.lock:
                self.inflight.pop(handle, None)
            if place.reap is not None:
                place.reap(box, self.keep(place.service, but=handle))
            stderr, stdout, head = self.read(box, live, wait_s=cj.SETTLE_S, final=True)
            if not head.get("found") and live.exec is not None:
                stderr, stdout = live.exec.stderr.decode(errors="replace"), live.exec.stdout.decode(errors="replace")
            if record is not None:
                self.problem(call, f"ended in time in the replay; the run kept it as job {record['id']}")
            return cj.joined(stderr, stdout), False, live.exit_code, None
        if record is not None:
            live.job = record["id"]
            with self.lock:
                self.inflight.pop(handle, None)
                self.live[live.job] = live
            stderr, stdout, _ = self.read(box, live)
            text = self.texts["kept"].format(job=live.job)
            return cj.fitted(text, cj.joined(stderr, stdout), self.cap, first=True), False, None, live.job
        self.stop(box, live, "stopped")
        with self.lock:
            self.inflight.pop(handle, None)
        self.problem(call, f"ran past {wait:.0f} s in the replay; it ended in {call.t1 - call.t0:.1f} s in the run")
        stderr, stdout, _ = self.read(box, live, wait_s=cj.SETTLE_S, final=True)
        return cj.joined(stderr, stdout), True, None, None

    def job_call(self, box, call: RecordedCall) -> str:
        """job_output or job_stop on the agent's replayed job, as command_jobs answered it."""
        job = str(call.arguments.get("job") or "")
        live = self.live.get(job)
        if live is None or live.owner != call.agent:
            if job in self.records and self.records[job].get("agent") == call.agent:
                self.problem(call, f"{call.tool} on {job}, which the replay never started")
            return self.texts["unknown"].format(job=job)
        record = self.records.get(job) or {}
        ended = record.get("ended")
        if record.get("status") == "exited" and ended is not None and float(ended) <= call.t0:
            live.thread.join(JOB_GRACE_S)
        if call.tool == "job_stop":
            self.stop(box, live, "stopped")
            stderr, stdout, _ = self.read(box, live, wait_s=cj.SETTLE_S)
        else:
            self.finished(live)
            stderr, stdout, _ = self.read(box, live, wait_s=cj.SETTLE_S if live.done else 0.0)
        return cj.fitted(self.status_line(live), cj.joined(stderr, stdout), self.cap)

    def status_line(self, live: Live) -> str:
        if live.status == "stopped":
            return self.texts["stopped"].format(job=live.job)
        if live.done:
            code = live.exit_code
            return self.texts["exited"].format(job=live.job, code=code if code is not None else "unknown")
        return self.texts["running"].format(job=live.job)

    def peer_shell(self, box, call: RecordedCall) -> tuple[str, bool]:
        """peer_shell as run_as_observer ran it: GRANT on the peer's machine, the command as the influencer's observer
        (its group PROC_GID in a run that hid /proc, else its own) in the peer's working directory through command,
        then the observer's REAP; the answer's JSON. A call the tool
        refused (no observer, or no such peer) keeps its recorded output."""
        peer, command = str(call.arguments.get("peer") or ""), str(call.arguments.get("command") or "")
        uid, places = self.observers.get(call.agent), self.places.get(peer)
        if uid is None or places is None or peer == call.agent:
            return call.result, False
        service = self.service_of(peer)
        granted = self.script(box, service, ps.GRANT, {"uid": uid, "tops": ps.tops(places)})
        if granted.rc != 0:
            raise RuntimeError(f"peer_shell grant on {service}: {granted.stderr.decode(errors='replace')[-500:]}")

        def reap(box, keep: list[str]) -> None:
            self.script(box, service, ps.REAP, {"uid": uid, "keep": keep})
        place = Place(service, f"{uid}:{self.observer_gid or uid}", places.get("workdir"),
                      {**ps.GIT_ENV, "HOME": ps.NO_HOME}, ["bash", "--login", "-c", command], None, reap)
        text, timed_out, code, job = self.command(box, call, place)
        try:
            gone = (json.loads(call.result) or {}).get("status") == "exited"
        except (ValueError, AttributeError):
            gone = False
        return json.dumps(ps.answer(peer, command, code, text, self.cap, gone, job), ensure_ascii=False), timed_out

    # ---------------------------------------------------------------- the end
    def end(self, box) -> None:
        """command_jobs.end_jobs: every replayed command still running is killed."""
        for live in list(self.live.values()) + list(self.inflight.values()):
            if not live.done:
                self.stop(box, live, "killed")
            self.finished(live)

    def report(self, played: dict[int, dict] | None = None) -> dict:
        """Each recorded job against its replay, and the problems: those met while playing, a job whose status or exit
        code differs or that never started, and a job call whose output differs (played: replay_run.play's results)."""
        jobs = []
        for job_id, record in sorted(self.records.items(), key=lambda item: int(item[0][1:])):
            live = self.live.get(job_id)
            replayed = None if live is None else {
                "status": live.status, "exit_code": live.exit_code,
                "seconds": round((live.ended or time.time()) - live.started, 1)}
            recorded = {"status": record.get("status"), "exit_code": record.get("exit_code"),
                        "seconds": round(float(record["ended"]) - float(record["started"]), 1)
                        if record.get("ended") and record.get("started") else None}
            jobs.append({"id": job_id, "agent": record.get("agent"), "tool": record.get("tool"),
                         "call_id": record.get("call_id"), "recorded": recorded, "replayed": replayed})
            if live is None:
                self.problem(None, f"job {job_id} was never started in the replay", job=job_id)
            elif (live.status, live.exit_code) != (record.get("status"), record.get("exit_code")):
                self.problem(None, f"job {job_id} {live.status} with code {live.exit_code} in the replay, "
                                   f"{record.get('status')} with code {record.get('exit_code')} in the run", job=job_id)
        for n, result in sorted((played or {}).items()):
            call = result["call"]
            if result["match"] == "differs" and (self.kept(call) or call.tool in JOB_TOOLS or call.tool == PEER_SHELL):
                self.problem(call, f"the {call.tool} call's output differs from the record")
        return {"jobs": jobs, "problems": list(self.problems)}
