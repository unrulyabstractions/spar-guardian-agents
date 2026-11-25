"""A stored creative-writing run played back in its sandbox: the staging rebuilt with the record's random values, each
recorded call as its agent's Linux user in the one container, the grading logs given their recorded names, the state
after every call and the end state against the stored one. The staging, the setup, the loopback guard, the probe, the
reap, the submit's snapshot and the scorer's readings are the run's own code (src/domains/creative/, src/sandbox/);
the pieces are CreativeDomain's (src/domains/creative/_creative_ladder.py).

    replayer = CreativeReplayer(run, calls)
    replayer.setup(box)                       # ReplayRefused unless the staging and the probe are the recorded ones
    output, timed_out = replayer.execute(box, call)
    output, state, named, unnamed = replayer.after(box, call, output)
    world = replayer.team_state(box)          # the files, the pieces they hold and the hand-in
    line = replayer.line_state(call, own, standing, world)   # the state a line of steps.jsonl carries
    replayer.end_state(box, keep)             # {"equal": bool, "parts": {...}, "counts": {...}}

It keeps SweReplayer's interface (src/replay/swe_replayer.py), the one replay_run.replay drives.

Each sample draws random values with `secrets`: the reference's directory /tmp/grading/ref/<8 hex> (run.json
creative.references), the review endpoint's port (creative.review_url) and each workspace's token (the probe's reading
of grading/.review, in the eval log's store). The staging takes them from the record (RecordedSecrets) and refuses a
run whose record lacks one. Each grading writes logs/logs_<8 hex>.json under a random name: after the call the
replayed log takes its recorded name (log_names), and the output is read with the recorded names.

The state after a call (StateReading): every file in the agents' places by its content id; for each piece
(segment1..N, and whole for a text that joins them, or that shows the reference where it is not cut) the files that
hold it, in the shared workspace, in each agent's own places and elsewhere; and each workspace's hand-in, writing.md.
A call's own line adds the checker's calls it made (each review endpoint call and grading log, with the pieces of the
submission), the pieces its recorded output showed and, for a host tool, the pieces its arguments said. An agent's own
places are its home, its own workspace in solo and the files it owns in the temporary directories. Every call runs
alone: the agents share one container, and in shared_task one workspace.
"""
from __future__ import annotations

import asyncio
import json
import re
import tempfile
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from typing import NamedTuple
from unittest import mock
from urllib.parse import urlsplit

import yaml
from inspect_ai.util import ExecResult

from src.configuration.swarm_config import SwarmConfig
from src.domains.benchmark_protocol import Plan
from src.domains.creative import _creative_honeypot as honeypot
from src.domains.creative import _creative_scoring as scoring
from src.domains.creative import creative_sandbox as sandbox
from src.domains.creative._creative_ladder import CreativeDomain
from src.domains.creative._creative_settings import (
    AGENT_IMAGE,
    DELIVERABLE,
    HAND_INS,
    PLANT_ROOT,
    REVIEW_CALLS,
    REVIEW_IMAGE,
    REVIEW_IP,
    SCORE_SO,
    TASK,
    WRITING_CHARS,
)
from src.domains.creative.creative_task import CreativeWriting
from src.measures import honeypot_fingerprints as prints_module
from src.measures.run_directory import RunDir
from src.measures.shell_commands import PYTHON_TOOLS, TOOLS, line_hits
from src.replay.output_match import match_of
from src.replay.recorded_calls import NEVER_RAN, RecordedCall, ReplayRefused, sample_store
from src.replay.replay_jobs import JobPlayer, Place
from src.replay.replay_sandbox import ReplaySandbox, timed_out, under_timeout
from src.sandbox import sandbox_hygiene as hygiene
from src.sandbox.agent_tools import TOOL_TIMEOUT_S, WORKDIR, Seat, python_command
from src.sandbox.docker_images import image_id
from src.sandbox.machines.agent_machines import DEFAULT_SERVICE
from src.sandbox.sandbox_probe import PROBE_STORE, run_commands, run_json

REVIEW_SERVICE = "review"
ROOTS = [WORKDIR, "/home", "/tmp", "/var/tmp", "/dev/shm"]
"""Where the agents can write: the workspaces, their homes and the temporary directories."""
SCORER_ROOTS = [WORKDIR, "/home", "/tmp"]
"""The roots the scorer reads for grading logs (_creative_scoring.creative_deliverables)."""
LOG_NAME = re.compile(r"logs_[0-9a-f]+\.json")
LOG_WINDOW_S = 5.0
"""How far outside a call's recorded window its grading log's recorded time may lie."""
TOKEN = re.compile(r'"token":\s*"([0-9a-f]{32})"')
WHOLE = "whole"
WORKSPACE, OTHER = "workspace", "other"
SCAN_BYTES = 1 << 20
SCAN = r'''
import hashlib, json, os, sys
spec = json.load(sys.stdin)
skip = tuple(s.rstrip("/") + "/" for s in spec["skip"])
out = []
for root in spec["roots"]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not (os.path.join(dirpath, d) + "/").startswith(skip))
        for name in sorted(filenames):
            path = os.path.join(dirpath, name)
            try:
                info = os.lstat(path)
                if not os.path.isfile(path) or os.path.islink(path):
                    continue
                with open(path, "rb") as f:
                    data = f.read(spec["max_bytes"] + 1)
            except OSError:
                continue
            entry = {"path": path, "uid": info.st_uid, "mtime": info.st_mtime,
                     "sha256": hashlib.sha256(data).hexdigest()}
            if len(data) <= spec["max_bytes"] and b"\0" not in data:
                entry["text"] = data.decode("utf-8", "replace")
            out.append(entry)
print(json.dumps(out))
'''
"""Every regular file under the roots but the skipped directories, as root: its owner, time and sha256, and its text
when it is no larger than max_bytes and holds no NUL."""
WRITING_KEYS = ("by", "exists", "size", "sha256", "uid", "verdict", "similarity", "reference_lines")
AT_END_KEYS = ("exists", "symlink", "size", "sha256", "uid", "verdict", "similarity")
LOG_KEYS = ("path", "uid", "verdict", "submission", "submission_sha256", "grader_config", "reference_tie")
CALL_KEYS = ("n", "owner", "verdict", "similarity", "reference_held", "reference_spoofed", "submission_chars",
             "reference_chars", "submission_sha256")
"""What the end state compares: of a hand-in, of a grading log and of a review endpoint call (times left out)."""
BLOCK_KEYS = ("layout", "candidate", "references", "review_url", "workspaces", "seats", "planted")
"""The keys of run.json's creative block the staging must reproduce."""
HOST_GRACE_S = 10
"""How long past a command's timeout the host waits for the in-container timeout to fire, as Inspect waits."""


class RecordedSecrets:
    """`secrets` for creative_sandbox.stage: the port and, in workspace order, each reference directory and token the
    run drew."""

    def __init__(self, port: int, folders: list[str], tokens: list[str]) -> None:
        self.port, self.folders, self.tokens = port, list(folders), list(tokens)

    def randbelow(self, _n: int) -> int:
        return self.port - sandbox.PORTS[0]

    def token_hex(self, nbytes: int) -> str:
        drawn = {4: self.folders, 16: self.tokens}.get(nbytes)
        if not drawn:
            raise ReplayRefused(f"the staging drew token_hex({nbytes}) beyond what the record holds")
        return drawn.pop(0)


def recorded_secrets(block: dict, store: dict) -> RecordedSecrets:
    """The run's port, reference directories and tokens: the port from the review URL, each workspace's directory from
    its reference's path, and its token from the probe's reading of grading/.review by a seat in that workspace."""
    port = urlsplit(block["review_url"]).port
    probe = store.get(PROBE_STORE) or {}
    tokens: dict[str, str] = {}
    for agent, seat in block["seats"].items():
        found = TOKEN.search(((probe.get(agent) or {}).get("config") or {}).get("out") or "")
        if found:
            tokens.setdefault(seat["workdir"], found.group(1))
    workdirs = list(block["workspaces"].values())
    missing = [w for w in workdirs if w not in tokens]
    if missing:
        raise ReplayRefused(f"the record holds no review token for {missing}: the probe never read grading/.review")
    if port is None:
        raise ReplayRefused(f"the record's review URL {block['review_url']} names no port")
    folders = [PurePosixPath(block["references"][w]).parent.name for w in workdirs]
    return RecordedSecrets(port, folders, [tokens[w] for w in workdirs])


class Staging(NamedTuple):
    """What a sample of the run receives: the staging tarball and the sidecar's config by (service, path), the
    compose file's services, the setup script and the block the staging made."""
    uploads: dict[tuple[str, str], bytes]
    services: dict[str, dict]
    setup: str
    block: dict


def plan_of(run_json: dict) -> Plan:
    """The run's plan, rebuilt from its config and roles by today's CreativeWriting."""
    config = SwarmConfig.model_validate({k: v for k, v in (run_json.get("config") or {}).items()
                                         if k in SwarmConfig.model_fields})
    return CreativeWriting(run_json["creative"]["row_id"]).plan(config, (run_json.get("swarm") or {}).get("roles"))


def staging_of(run_json: dict, secrets: RecordedSecrets) -> Staging:
    """creative_sandbox's stage, stage_files and write_compose with the record's random values, their files written
    to a temporary folder in place of the build and read back. The agents kept out of the shared writing are the
    record's (creative.shared_result), never today's rule: none in a run made before it."""
    plan = plan_of(run_json)
    row = honeypot.row_of(run_json["creative"]["row_id"])
    with mock.patch.object(sandbox, "secrets", secrets):
        staged = sandbox.stage(plan, row, shared_result=run_json["creative"].get("shared_result"))
    with tempfile.TemporaryDirectory(prefix="creative-replay-") as folder, \
            mock.patch.object(sandbox, "BUILD", Path(folder)):
        files = sandbox.stage_files(staged, row["id"])
        services = yaml.safe_load(sandbox.write_compose().read_text())["services"]
        uploads = {}
        for target, source in files.items():
            service, _, path = target.rpartition(":")
            uploads[(service or DEFAULT_SERVICE, path)] = Path(source).read_bytes()
    return Staging(uploads, services, sandbox.setup_script(plan), staged["block"])


def kept_submits(run_json: dict, calls: list[RecordedCall]) -> dict[str, str]:
    """{call id: the recorded snapshot} for each submit that went through: the team's (or the lone agent's) and each
    solo_task agent's own, at that agent's first submit call that raised no error."""
    hand_in = run_json.get("team_submission") or {}
    wanted = {agent: kept.get("snapshot") for agent, kept in (hand_in.get("own") or {}).items()}
    if hand_in.get("agent"):
        wanted[hand_in["agent"]] = hand_in.get("snapshot")
    out = {}
    for call in calls:
        if call.tool == "submit" and call.error is None and wanted.get(call.agent):
            out[call.id] = wanted.pop(call.agent)
    return out


def log_record(entry: dict) -> dict:
    """A grading log's fields from its scanned text; {} when it is no JSON."""
    try:
        record = json.loads(entry.get("text") or "")
    except ValueError:
        return {}
    return record if isinstance(record, dict) else {}


def is_log(path: str) -> bool:
    """A grading log: logs/logs_<hex>.json."""
    path = PurePosixPath(path)
    return path.parent.name == "logs" and LOG_NAME.fullmatch(path.name) is not None


def log_names(call: RecordedCall, output: str, new: list[dict], recorded: list[dict],
              taken: set[str]) -> dict[str, str]:
    """{replayed path: recorded path} for the call's new grading logs (scanned entries): by the names its recorded and
    replayed outputs give, in order; then by the scorer's list of logs (`recorded`), the first not yet taken in the
    same directory, of the same owner and submission, written in the call's window."""
    out = {}
    by_name = {PurePosixPath(entry["path"]).name: entry["path"] for entry in new}
    for replayed, wanted in zip(LOG_NAME.findall(output), LOG_NAME.findall(call.result)):
        if replayed in by_name:
            out[by_name[replayed]] = str(PurePosixPath(by_name[replayed]).with_name(wanted))
    claimed = set(out.values()) | taken
    for entry in new:
        if entry["path"] in out:
            continue
        folder, sha = str(PurePosixPath(entry["path"]).parent), log_record(entry).get("submission_sha256")
        match = next((log["path"] for log in recorded if log["path"] not in claimed
                      and str(PurePosixPath(log["path"]).parent) == folder and log.get("uid") == entry["uid"]
                      and log.get("submission_sha256") == sha
                      and call.t0 - LOG_WINDOW_S <= (log.get("mtime") or 0) <= call.t1 + LOG_WINDOW_S), None)
        if match:
            out[entry["path"]] = match
            claimed.add(match)
    return out


def compared(recorded: dict, replayed: dict, keys: tuple[str, ...]) -> dict:
    """{key: (recorded, replayed)} for each key whose values differ."""
    return {k: (recorded.get(k), replayed.get(k)) for k in keys if recorded.get(k) != replayed.get(k)}


def list_differs(recorded: list[dict], replayed: list[dict], keys: tuple[str, ...]) -> list[dict]:
    """Each position whose entries differ on these keys, an entry only one side holds included."""
    out = []
    for i in range(max(len(recorded), len(replayed))):
        a = recorded[i] if i < len(recorded) else {}
        b = replayed[i] if i < len(replayed) else {}
        if differs := compared(a, b, keys):
            out.append({"at": i, **differs})
    return out


def unordered(items: list | None) -> list[str]:
    return sorted(json.dumps(item, sort_keys=True) for item in items or [])


class ContainerBox:
    """One container of the replay's sandbox behind the exec of Inspect's docker sandbox, which the run's own probe,
    guard, reap, snapshot and scorer scripts take as their box. The command runs through ReplaySandbox.run as `user`
    in `cwd`. With a timeout it runs under the in-container timeout and raises TimeoutError when that fires, as
    Inspect's does."""

    def __init__(self, box: ReplaySandbox, service: str = DEFAULT_SERVICE) -> None:
        self.box, self.service = box, service

    async def exec(self, cmd: list[str], input: str | bytes | None = None, cwd: str | None = None,
                   user: str | None = None, timeout: int | None = None) -> ExecResult[str]:
        data = input.encode() if isinstance(input, str) else input
        argv = cmd if timeout is None else under_timeout(cmd, timeout)
        done = await asyncio.to_thread(self.box.run, self.service, argv, stdin=data, workdir=cwd, user=user,
                                       timeout=None if timeout is None else timeout + HOST_GRACE_S)
        if timeout is not None and timed_out(done, timeout):
            raise TimeoutError(f"Command timed out after {timeout} seconds")
        return ExecResult(done.rc == 0, done.rc, done.stdout.decode("utf-8", "replace"),
                          done.stderr.decode("utf-8", "replace"))


class StateReading:
    """The pieces in the files of a creative run's sandbox, read by CreativeDomain, and whose place each file is."""

    def __init__(self, domain: CreativeDomain, block: dict) -> None:
        self.domain, self.block = domain, block
        self.seats = sandbox.seats_of(block)
        self.agents = sorted(self.seats)
        self.pieces = [*sorted(domain.prints.pieces), WHOLE]
        homes = {seat.home: agent for agent, seat in self.seats.items() if seat.home}
        spaces = {workdir: WORKSPACE if owner == "team" else owner for owner, workdir in block["workspaces"].items()}
        self.places = sorted({**homes, **spaces}.items(), key=lambda item: -len(item[0]))
        self.uids = {seat.uid: agent for agent, seat in self.seats.items()}
        self.cache: dict[str, list[str]] = {}       # sha256 -> the pieces its text holds

    def pieces_of(self, text: str) -> list[str]:
        """The segments a text shows (CreativeDomain.pieces_seen), and whole where it joins every segment, or shows
        the reference where it is not cut."""
        whole = self.domain.joined(text) if self.domain.prints.pieces else self.domain.shows(text)
        return [*self.domain.pieces_seen(text), *([WHOLE] if whole else [])]

    def pieces_in(self, entry: dict) -> list[str]:
        """The pieces of a scanned file, once per content."""
        if entry["sha256"] not in self.cache:
            self.cache[entry["sha256"]] = self.pieces_of(entry["text"]) if "text" in entry else []
        return self.cache[entry["sha256"]]

    def place(self, path: str, uid: int) -> str:
        """Whose place a file is in: an agent's (its home, its own workspace, a temporary file it owns), the shared
        workspace (WORKSPACE) or no agent's (OTHER)."""
        for folder, owner in self.places:
            if path.startswith(folder.rstrip("/") + "/"):
                return owner
        return self.uids.get(uid, OTHER)

    def world(self, files: list[dict]) -> dict:
        """The files by content id, the files of each place that hold each piece, and each workspace's hand-in."""
        pieces = {p: {WORKSPACE: [], "agents": {a: [] for a in self.agents}, OTHER: []} for p in self.pieces}
        for entry in files:
            where = self.place(entry["path"], entry["uid"])
            for piece in self.pieces_in(entry):
                slot = pieces[piece]
                (slot["agents"][where] if where in slot["agents"] else slot[where]).append(entry["path"])
        by_path = {f["path"]: f for f in files}
        hand_in = {}
        for workdir in self.block["workspaces"].values():
            entry = by_path.get(f"{workdir}/{DELIVERABLE}")
            hand_in[workdir] = {"sha256": entry["sha256"][:12], "pieces": self.pieces_in(entry)} if entry else None
        return {"files": {f["path"]: f["sha256"][:12] for f in files}, "pieces": pieces, "hand_in": hand_in}

    def log_check(self, entry: dict, files: list[dict]) -> dict:
        """A grading log with the pieces of the submission it names: the file it names, read in the directory the
        grader ran in, when that file still holds the logged sha256; None when it does not."""
        record = log_record(entry)
        folder = PurePosixPath(entry["path"]).parents[1]
        submitted = next((f for f in files if f["path"] == str(folder / str(record.get("submission") or ""))
                          and f["sha256"] == record.get("submission_sha256")), None)
        return {"via": "log", "path": entry["path"], "verdict": record.get("verdict"),
                "submission": record.get("submission"), "submission_sha256": record.get("submission_sha256"),
                "pieces": self.pieces_in(submitted) if submitted else None}

    def review_check(self, call: dict, recorded: dict | None) -> dict:
        """A review endpoint call with the pieces of its submission, and whether it is the recorded call of its
        number."""
        return {"via": "review", "n": call["n"], "owner": call["owner"], "verdict": call["verdict"],
                "similarity": call["similarity"], "reference_held": call["reference_held"],
                "submission_sha256": call["submission_sha256"], "pieces": self.pieces_of(call.get("submission") or ""),
                "recorded": recorded is not None and not compared(recorded, call, CALL_KEYS)}

    def line(self, call: RecordedCall, state: dict | None, world: dict) -> dict:
        """A line's state: the world, the call's own checks (none when it was not played), the pieces its recorded
        output showed and, for a host tool, those its arguments said."""
        said = "" if call.tool in TOOLS else json.dumps(call.arguments, ensure_ascii=False)
        return {**world, "checks": (state or {}).get("checks", []), "shown": self.pieces_of(call.result),
                "said": self.pieces_of(said) if said else []}


def run_async(coroutine):
    return asyncio.run(coroutine)


class CreativeReplayer:
    task = TASK

    def __init__(self, run: RunDir, calls: list[RecordedCall]) -> None:
        if run.eval_log is None:
            raise ReplayRefused(f"{run.path.name}: no eval log, so no recorded review token")
        self.run, self.calls = run, calls
        self.block = run.run_json["creative"]
        self.store = sample_store(run.eval_log)
        self.staging = staging_of(run.run_json, recorded_secrets(self.block, self.store))
        self.reading = StateReading(CreativeDomain(run.run_json), self.block)
        self.seats: dict[str, Seat] = self.reading.seats
        self.agents = self.reading.agents
        self.pieces = self.reading.pieces
        self.plant_dirs = sorted({str(PurePosixPath(p).parent) for p in self.block["references"].values()})
        self.recorded_logs = list(self.block.get("grader_logs") or [])
        self.recorded_review = {c["n"]: c for c in self.block.get("review_calls") or []}
        self.submits = kept_submits(run.run_json, calls)
        self.kept: dict[str, str] = {}          # workspace -> the sha256 of the writing.md its submit kept
        self.review_seen = 0
        self.logs_seen: set[str] = set()
        self.world: dict | None = None
        self.jobs = JobPlayer.of(run, calls, service_of=self.service_of)    # None before the round clock

    # ---------------------------------------------------------------- the sandbox
    def services(self) -> dict[str, dict]:
        return self.staging.services

    def volumes(self) -> dict[str, dict]:
        """The compose project's volumes: none, the agents share one container."""
        return {}

    def image(self) -> dict:
        recorded = self.block.get("images") or {}
        ids = {tag: image_id(tag) for tag in (AGENT_IMAGE, REVIEW_IMAGE)}
        return {"tag": AGENT_IMAGE, "id": ids[AGENT_IMAGE], "run_id": recorded.get(AGENT_IMAGE),
                "own": all(ids[t] and ids[t] == recorded.get(t) for t in ids), "review_id": ids[REVIEW_IMAGE],
                "review_run_id": recorded.get(REVIEW_IMAGE)}

    @property
    def port(self) -> int:
        return urlsplit(self.block["review_url"]).port

    def setup(self, box: ReplaySandbox) -> dict:
        """The sample's files and setup script, the loopback guard and the probe, as the run had them. Refused when the
        staging's block is not run.json's, or the probe reads another grading config or token than the recorded."""
        problems = [f"{key} {self.staging.block.get(key)!r}, the run {self.block.get(key)!r}"
                    for key in BLOCK_KEYS if self.staging.block.get(key) != self.block.get(key)]
        if problems:
            raise ReplayRefused("the staging does not reproduce the run's: " + "; ".join(problems))
        for (service, path), data in self.staging.uploads.items():
            box.must(service, f"mkdir -p {PurePosixPath(path).parent} && cat > {path}.part && mv {path}.part {path}",
                     f"upload {path}", stdin=data)
        box.must(DEFAULT_SERVICE, self.staging.setup, "the setup script")
        toolbox = ContainerBox(box)
        evidence: dict = {"uploads": sorted(path for _, path in self.staging.uploads)}
        if self.block["layout"] != "lone":
            first, second = [seat.user for seat in self.seats.values()][:2]
            evidence["loopback"] = run_async(hygiene.guard_loopback(first, second, allow=[f"{REVIEW_IP}:{self.port}"],
                                                                    box=toolbox))
        recorded = self.store.get(PROBE_STORE) or {}
        evidence["probe"] = {}
        for agent, (seat, commands, fields) in scoring.probe(
                SimpleNamespace(metadata={"cheatbench": {"creative": self.block}})).items():
            found = run_async(run_commands(commands, fields, user=seat.user, cwd=seat.workdir, box=toolbox))
            mine = recorded.get(agent) or {}
            evidence["probe"][agent] = {name: match_of((mine.get(name) or {}).get("out") or "", found[name]["out"])
                                        if name in mine else "unrecorded" for name in commands}
        wrong = [agent for agent, checks in evidence["probe"].items() if checks["config"] != "exact"]
        if wrong:
            raise ReplayRefused(f"the probe reads another grading config or token than the run's for {wrong}")
        return evidence

    # ---------------------------------------------------------------- the calls
    def in_sandbox(self, call: RecordedCall) -> bool:
        """A shell or python call that ran (recorded_calls.NEVER_RAN), and a submit that kept writing.md; since the
        round clock also job_output, job_stop and peer_shell (replay_jobs.py)."""
        if self.jobs is not None and self.jobs.handles(call):
            return True
        return (call.tool in TOOLS and call.error not in NEVER_RAN) or call.id in self.submits

    def shared(self, call: RecordedCall) -> bool:
        return True

    @staticmethod
    def service_of(agent: str) -> str:
        return DEFAULT_SERVICE

    def place(self, call: RecordedCall) -> Place:
        """Where the run's command ran since the round clock: the agent's seat in the one machine (agent_tools.run),
        with the seat's reap sparing the commands still running."""
        seat, code = self.seats[call.agent], self.command(call)
        python = call.tool in PYTHON_TOOLS
        argv = ["bash", "--login", "-c", python_command(seat, self.seats) if python else code]

        def reap(box: ReplaySandbox, keep: list[str]) -> None:
            run_async(hygiene.reap(seat.user, box=ContainerBox(box), keep=keep))
        return Place(DEFAULT_SERVICE, seat.user, seat.workdir, {}, argv, code.encode() if python else None,
                     reap if seat.reap else None)

    @staticmethod
    def command(call: RecordedCall) -> str:
        return str(call.arguments.get("command") or call.arguments.get("code") or "")

    def execute(self, box: ReplaySandbox, call: RecordedCall) -> tuple[str, bool]:
        """The call as the agent's tool ran it (agent_tools.run): `bash --login -c` as the seat's user in its
        directory, run_python's code on python's stdin, the user's processes reaped after it. A submit that went
        through keeps writing.md as _creative_scoring.snapshot_writing does (snapshot)."""
        if call.id in self.submits:
            return self.snapshot(box, call), False
        if self.jobs is not None:
            return self.jobs.execute(box, call, self)
        seat, code = self.seats[call.agent], self.command(call)
        python = call.tool in PYTHON_TOOLS
        argv = ["bash", "--login", "-c", python_command(seat, self.seats) if python else code]
        toolbox = ContainerBox(box)

        async def run() -> tuple[str, bool]:
            try:
                done = await toolbox.exec(argv, input=code if python else None, timeout=TOOL_TIMEOUT_S,
                                          user=seat.user, cwd=seat.workdir)
                return (f"{done.stderr}\n" if done.stderr else "") + done.stdout, False
            except TimeoutError:
                return "", True
            finally:
                if seat.reap:
                    await hygiene.reap(seat.user, box=toolbox)
        return run_async(run())

    def snapshot(self, box: ReplaySandbox, call: RecordedCall) -> str:
        """Keep the agent's workspace's writing.md as the submit did. The output is the recorded one when the kept copy
        has the recorded sha256, else the kept sha256 or the failure."""
        workdir = self.seats[call.agent].workdir
        try:
            sha = run_async(run_json(scoring.WRITING_SNAPSHOT, args=[f"{workdir}/{DELIVERABLE}", HAND_INS],
                                     box=ContainerBox(box)))
        except RuntimeError as error:
            return f"snapshot failed: {error}"
        self.kept[workdir] = sha
        return call.result if sha == self.submits[call.id] else f"kept {sha}, the run {self.submits[call.id]}"

    def initial(self, box: ReplaySandbox) -> dict[str, dict]:
        self.world = None
        self.logs_seen = {entry["path"] for entry in self.scan(box) if is_log(entry["path"])}
        return {agent: {"checks": []} for agent in self.agents}

    def after(self, box: ReplaySandbox, call: RecordedCall, output: str) -> tuple[str, dict, list[dict], list[dict]]:
        """After the call: its grading logs given their recorded names, the output read with them, the world, and
        the checker's calls it made. Returns (output, state, named, unnamed): the logs renamed, and those that kept
        their own name."""
        files = self.scan(box)
        new = [entry for entry in files if is_log(entry["path"]) and entry["path"] not in self.logs_seen]
        renames = log_names(call, output, new, self.recorded_logs, self.logs_seen)
        named, unnamed, logs = [], [], set()
        where = {"n": call.n, "agent": call.agent, "k": call.k}
        for entry in new:
            path, target = entry["path"], renames.get(entry["path"])
            moved = target not in (None, path) and \
                box.exec(DEFAULT_SERVICE, f"mv -n {path} {target} && test ! -e {path}").rc == 0
            if moved:
                named.append({**where, "old": path, "new": target})
                path = target
            elif target != path:
                unnamed.append({**where, "path": path})
            logs.add(path)
        self.logs_seen |= logs
        for change in named:
            output = output.replace(PurePosixPath(change["old"]).name, PurePosixPath(change["new"]).name)
        if named:
            files = self.scan(box)
        self.world = self.reading.world(files)
        checks = [*self.review_checks(box), *(self.reading.log_check(entry, files) for entry in files
                                              if entry["path"] in logs)]
        return output, {"checks": checks}, named, unnamed

    def review_checks(self, box: ReplaySandbox) -> list[dict]:
        """The review endpoint's calls since the last call (StateReading.review_check)."""
        done = box.exec(REVIEW_SERVICE, f"tail -n +{self.review_seen + 1} {REVIEW_CALLS} 2>/dev/null")
        calls = [json.loads(line) for line in done.stdout.decode(errors="replace").splitlines() if line.strip()]
        self.review_seen += len(calls)
        return [self.reading.review_check(c, self.recorded_review.get(c["n"])) for c in calls]

    # ---------------------------------------------------------------- the state
    def scan(self, box: ReplaySandbox) -> list[dict]:
        spec = {"roots": ROOTS, "skip": self.plant_dirs, "max_bytes": SCAN_BYTES}
        return run_async(run_json(SCAN, stdin=spec, box=ContainerBox(box)))

    def team_state(self, box: ReplaySandbox) -> dict:
        """The world after the last call (after reads it), or read now before the first."""
        if self.world is None:
            self.world = self.reading.world(self.scan(box))
        return self.world

    def line_state(self, call: RecordedCall, own: dict | None, standing: dict, world: dict) -> dict:
        """The state a line of steps.jsonl carries (StateReading.line): `own` is the call's own (after's) when it was
        played, else None; `world` is team_state's as the last played call left it. `standing`, the agent's state as
        it stands, adds nothing: the world holds every agent's files."""
        return self.reading.line(call, own, world)

    # ---------------------------------------------------------------- the end state
    def end_state(self, box: ReplaySandbox, keep: Path | None = None) -> dict:
        """The scorer's reading at the end of the run, compared with the stored one: the processes and shared files
        (hygiene.sandbox_state), each workspace's hand-in and every grading log (writing_files.py), every review
        endpoint call, the honeypot probe by owner, passed and each submit's snapshot. "equal" is all of them. With
        `keep` the reading is kept there as end.json."""
        toolbox = ContainerBox(box)
        owners = {agent: seat.uid for agent, seat in self.seats.items()}
        dirs = list(dict.fromkeys([*hygiene.STATE_DIRS, WORKDIR, PLANT_ROOT]))
        state = run_async(hygiene.sandbox_state(owners, dirs, box=toolbox))
        row = honeypot.row_of(self.block["row_id"])
        spec = {"workdirs": list(self.block["workspaces"].values()),
                "kept": {w: f"{HAND_INS}/{sha}" for w, sha in self.kept.items()}, "roots": SCORER_ROOTS,
                "skip": self.plant_dirs, "deliverable": DELIVERABLE, "reference": honeypot.reference_text(row),
                "tie_threshold": honeypot.tie_threshold(), "score_so": SCORE_SO, "keep": WRITING_CHARS}
        found = run_async(run_json(scoring.WRITING_FILES, stdin=spec, box=toolbox))
        calls = [json.loads(line) for line in box.exec(REVIEW_SERVICE, f"cat {REVIEW_CALLS} 2>/dev/null").stdout
                 .decode(errors="replace").splitlines() if line.strip()]
        roots = list(dict.fromkeys([*self.block["workspaces"].values(), "/home", "/tmp"]))
        probed = prints_module.by_owner(run_async(prints_module.probe(
            roots, self.reading.domain.prints, skip=self.plant_dirs, box=toolbox)), owners)
        if keep is not None:
            keep.mkdir(parents=True, exist_ok=True)
            (keep / "end.json").write_text(json.dumps({"sandbox": state, "writing_files": found, "review_calls": calls,
                                                       "honeypot_probe": probed}, indent=1))
        delivered = [w.get("verdict") for w in found["writings"].values() if w.get("exists")]
        parts = {"writings": self.writings_differ(found["writings"]),
                 "grader_logs": list_differs(self.recorded_logs, found["logs"], LOG_KEYS),
                 "review_calls": list_differs(list(self.recorded_review.values()), calls, CALL_KEYS),
                 "honeypot_probe": self.probe_differs(probed),
                 "shared_files": self.files_differ(state),
                 "survivors": {a: p["survivors"] for a, p in (state.get("processes") or {}).items()
                               if p.get("survivors")},
                 "passed": compared({"passed": self.block.get("passed")},
                                    {"passed": "tie" in delivered if delivered else None}, ("passed",)),
                 "snapshots": {cid: sha for cid, sha in self.submits.items() if sha not in self.kept.values()}}
        return {"equal": not any(parts.values()), "parts": parts,
                "counts": {"grader_logs": [len(self.recorded_logs), len(found["logs"])],
                           "review_calls": [len(self.recorded_review), len(calls)],
                           "scanned": [(self.run.run_json.get("honeypot_probe") or {}).get("scanned"),
                                       probed.get("scanned")]}}

    def writings_differ(self, found: dict) -> dict:
        """Each workspace's hand-in, and the file the run ended with, where they differ from run.json's."""
        out = {}
        for workdir, recorded in (self.block.get("writings") or {}).items():
            replayed = found.get(workdir) or {}
            replayed = {**replayed,
                        "reference_lines": line_hits(replayed.get("text") or "", self.reading.domain.prints.lines)}
            differs = compared(recorded, replayed, WRITING_KEYS)
            at_end = compared(recorded.get("at_end") or {}, replayed.get("at_end") or {}, AT_END_KEYS)
            if differs or at_end:
                out[workdir] = {**differs, **({"at_end": at_end} if at_end else {})}
        return out

    def probe_differs(self, probed: dict) -> dict:
        """The honeypot probe's matches and line hits by owner that differ from run.json's, in any order."""
        recorded = self.run.run_json.get("honeypot_probe") or {}
        pairs = {f"agents.{a}": ((recorded.get("agents") or {}).get(a) or {}, (probed.get("agents") or {}).get(a) or {})
                 for a in {*(recorded.get("agents") or {}), *(probed.get("agents") or {})}}
        pairs.update({group: (recorded.get(group) or {}, probed.get(group) or {}) for group in ("remote", "shared")})
        return {f"{owner}.{key}": (a.get(key), b.get(key)) for owner, (a, b) in pairs.items()
                for key in ("matches", "line_hits") if unordered(a.get(key)) != unordered(b.get(key))}

    def files_differ(self, state: dict) -> dict:
        """The shared directories' file counts by owner, and the agents' paths in any order, against run.json's."""
        recorded = ((self.run.run_json.get("sandbox") or {}).get("end") or {}).get("shared_files") or {}
        out = {}
        for folder, want in recorded.items():
            got = (state.get("shared_files") or {}).get(folder) or {}
            if (want.get("files"), want.get("by_owner")) != (got.get("files"), got.get("by_owner")) or \
                    unordered(want.get("agent_paths")) != unordered(got.get("agent_paths")):
                out[folder] = {"recorded": {k: want.get(k) for k in ("files", "by_owner")},
                               "replayed": {k: got.get(k) for k in ("files", "by_owner")}}
        return out
