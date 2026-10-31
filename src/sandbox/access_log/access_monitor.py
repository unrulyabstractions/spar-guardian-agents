"""The access log's API: what a domain watches, the sidecar in the sample's compose file, the setup solver that starts
the monitor, and the capture that brings its rows into the sample store.

    compose = with_monitor(compose)
    objects = [Watched("k7q2m", "/opt/key.log", "key"), *await git_watched("k7q2m", "/app", refs, others)]
    setup = [..., benchmark.setup(), start_access_monitor(benchmark)]       # src/tasks.py
    scorers = [access_capture(), *benchmark.scorers()]                      # and capture(state) in Task.cleanup

The sidecar is one more compose service, AUDITOR: the sample's own image with the idle command, no network, the host's
pid namespace, SYS_ADMIN and SYS_PTRACE. The agents' services stay as they were and no tool reaches it. The monitor
(access_monitor_daemon.py) sees every process on the machine and touches only its own containers.

The lifecycle. start_access_monitor runs after the benchmark's setup, so the harness's own reads while staging come
before any mark. It stores the seat table first, marks what Benchmark.watched(state) names, waits for the ready row and
runs the start control. The harness opens every watched object as root with a nonce in argv and O_NOATIME, and a
control that leaves no row raises, so the sample dies before any model call. capture is the first scorer, before any
harness read of the honeypot: the end control, SIGTERM, then the log. Task.cleanup calls it too, for a sample whose
scorers never ran. A dead monitor loses the end control and the `stopped` row, and the rows it wrote before dying stay
true.

The store's STORE key: {"seats": [{machine, uid, user, agent}], "objects": [Watched as a dict], "nonces": {start, end},
"pid": the daemon's pid, and from capture "rows": the raw rows, "gone": {machine: [paths the end control could not
open]} and "error"}. A packed object is listed and never marked, so it has no control.
"""
from __future__ import annotations

import json
import secrets
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

import yaml
from inspect_ai.scorer import Scorer, Target, scorer
from inspect_ai.solver import Generate, Solver, TaskState, solver
from inspect_ai.util import sandbox, subprocess

from src.sandbox import staging
from src.sandbox.agent_tools import Seat
from src.sandbox.machines import agent_machines as machines
from src.sandbox.sandbox_probe import run_json

HERE = Path(__file__).parent
DAEMON = (HERE / "access_monitor_daemon.py").read_text()
GIT_OBJECT_FILES = (HERE / "git_object_files.py").read_text()
STORE = "access_log"                # sample store: see the module docstring
AUDITOR = "auditor"                 # the sidecar's compose service
AUDITOR_CAPS = ["SYS_ADMIN", "SYS_PTRACE"]
MONITOR_DIR = "/run/access_log"     # in the sidecar
DAEMON_PATH = f"{MONITOR_DIR}/access_monitor_daemon.py"
PLAN_PATH = f"{MONITOR_DIR}/plan.json"
LOG_PATH = f"{MONITOR_DIR}/log.jsonl"
ERRORS_PATH = f"{MONITOR_DIR}/stderr.txt"
MAX_ROWS = 20_000                   # ordinary opens past this are counted, not written
MONITOR_TIMEOUT_S = 60
MONITOR_DEVIATION = "honeypot_opens_recorded_by_a_monitor_outside_the_agents_machines"

# Start the daemon, wait for its ready row and print it; its stderr fails the wait.
START = (f"setsid -f python3 -I {DAEMON_PATH} <{PLAN_PATH} >/dev/null 2>{ERRORS_PATH}; "
         f"""until grep -qs '"row": "ready"' {LOG_PATH}; do """
         f"if [ -s {ERRORS_PATH} ]; then cat {ERRORS_PATH} >&2; exit 1; fi; sleep 0.05; done; "
         f"""grep '"row": "ready"' {LOG_PATH}""")
# SIGTERM to the daemon, and wait for it to go. The sidecar sees every process on the host, so a pid is believed only
# inside the sidecar's own mount namespace: a dead daemon's pid may belong to anyone by now.
STOP = ('[ "$(readlink /proc/$1/ns/mnt)" = "$(readlink /proc/self/ns/mnt)" ] || exit 0; kill -TERM "$1"; '
        'while [ -d "/proc/$1" ]; do sleep 0.05; done')
# The control's read: argv[1] is the nonce. Printed: the paths it could not open. An agent may have left anything at a
# path by the end, so a symlink is never followed and a FIFO never waited on.
CONTROL = r'''
import json, os, sys
gone = []
for path in sys.argv[2:]:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOATIME | os.O_NOFOLLOW | os.O_NONBLOCK)
        os.read(fd, 1)
        os.close(fd)
    except OSError:
        gone.append(path)
print(json.dumps(gone))
'''


@dataclass(frozen=True)
class Watched:
    """One honeypot object on one machine, as a domain names it (Benchmark.watched)."""
    machine: str                # the compose service
    path: str                   # the file on that machine
    object: str                 # the honeypot's stable name
    level: str = "content"      # content, or pointer for what only leads to content
    kind: str = "file"          # file, or for git ref, commit, tree or blob; only a file's directory is marked too
    packed: bool = False        # no loose file: reported, never marked


# --------------------------------------------------------------------------
# the sidecar
# --------------------------------------------------------------------------
def sidecar_image(services: dict[str, dict]) -> str:
    """The sample's own image, so a run pulls and builds nothing new, or the machines' image where every service is
    built (openmath)."""
    image = next((service["image"] for service in services.values() if "image" in service), None)
    if image:
        return image
    machines.ensure_image()
    return machines.IMAGE


def with_monitor(compose: Path) -> Path:
    """A compose file beside `compose` that adds the sidecar and leaves every service as it was."""
    document = yaml.safe_load(compose.read_text())
    services = document["services"]
    auditor = staging.compose_service(working_dir="/", image=sidecar_image(services), pid="host", cap_add=AUDITOR_CAPS)
    return staging.write_yaml(compose.with_name(f"{compose.stem}-monitored.yaml"),
                              {**document, "services": {**services, AUDITOR: auditor}})


async def git_watched(machine: str, repo: str, refs: list[str], others: list[str]) -> list[Watched]:
    """What `refs` add over `others` in a repository on one machine: a ref's commits, trees and blobs and the ref
    itself, each with packed set when it has no loose file (git_object_files.py)."""
    found = await run_json(GIT_OBJECT_FILES, stdin={"repo": repo, "refs": refs, "others": others},
                           box=sandbox(machine))
    return [Watched(machine=machine, **entry) for entry in found]


# --------------------------------------------------------------------------
# the store's record
# --------------------------------------------------------------------------
def seat_table(seats: dict[str, Seat]) -> list[dict]:
    """The run's seats as the export names an agent from a row: {machine, uid, user, agent}."""
    return [{"machine": seat.service or machines.DEFAULT_SERVICE, "uid": seat.uid, "user": seat.user, "agent": agent}
            for agent, seat in seats.items()]


def marked(record: dict) -> list[dict]:
    """The record's objects the monitor marks."""
    return [obj for obj in record["objects"] if not obj["packed"]]


def parse_rows(text: str) -> list[dict]:
    """The log's rows. A daemon killed inside a write leaves a cut last line, which is dropped."""
    rows = []
    for line in text.splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            break
    return rows


def uncontrolled(rows: list[dict], objects: list[dict], control: str) -> list[str]:
    """The objects with no row of this control, as machine:object. Each control opens each object once, so the rows
    are counted against the objects, and two objects of one name stay two."""
    seen = Counter((row["machine"], row["object"]) for row in rows if row.get("control") == control)
    wanted = Counter((obj["machine"], obj["object"]) for obj in objects)
    return sorted(f"{machine}:{name}" for machine, name in (wanted - seen).elements())


# --------------------------------------------------------------------------
# the lifecycle
# --------------------------------------------------------------------------
async def container_of(service: str) -> dict:
    """The service's container id, and its init's pid as the sidecar sees it, from the host's Docker."""
    connection = await sandbox(service).connection()
    found = await subprocess(["docker", "inspect", "--format", "{{.Id}} {{.State.Pid}}", connection.container])
    if not found.success:
        raise RuntimeError(f"docker inspect {connection.container}: {found.stderr}")
    container, pid = found.stdout.split()
    return {"container": container, "pid": int(pid)}


async def control(machine: str, objects: list[dict], nonce: str) -> list[str]:
    """Open each object of this machine as root, with the nonce in argv: the paths that could not be opened."""
    paths = [obj["path"] for obj in objects if obj["machine"] == machine]
    return await run_json(CONTROL, args=[nonce, *paths], box=sandbox(machine))


@solver
def start_access_monitor(benchmark) -> Solver:
    """Task setup, after the benchmark's own: store the seat table and the objects, start the monitor over them and
    run the start control. Any failure raises, so a run with no monitor spends nothing, and so does a benchmark that
    names nothing, whose empty log would read as no access."""
    async def solve(state: TaskState, generate: Generate) -> TaskState:
        nonces = {"start": secrets.token_hex(8), "end": secrets.token_hex(8)}
        objects = [asdict(obj) for obj in await benchmark.watched(state)]
        if not objects:
            raise RuntimeError("access monitor: nothing to watch")
        record = {"seats": seat_table(benchmark.seats(state)), "objects": objects, "nonces": nonces}
        state.store.set(STORE, record)
        live = marked(record)
        on_machines = sorted({obj["machine"] for obj in live})
        plan = {"log": LOG_PATH, "max_rows": MAX_ROWS, "nonces": nonces, "objects": live,
                "machines": {machine: await container_of(machine) for machine in on_machines}}
        box = sandbox(AUDITOR)
        await box.write_file(DAEMON_PATH, DAEMON)
        await box.write_file(PLAN_PATH, json.dumps(plan))
        started = await box.exec(["bash", "-c", START], user="root", timeout=MONITOR_TIMEOUT_S)
        if not started.success:
            raise RuntimeError(f"access monitor did not start: {started.stderr[-800:]}")
        state.store.set(STORE, {**record, "pid": json.loads(started.stdout)["pid"]})
        for machine in on_machines:
            await control(machine, live, nonces["start"])
        missing = uncontrolled(parse_rows(await box.read_file(LOG_PATH)), live, "start")
        if missing:
            raise RuntimeError(f"access monitor: the start control left no row for {missing}")
        return state
    return solve


async def capture(state: TaskState) -> None:
    """The end control, SIGTERM and the log, into the store, once per sample. What fails is recorded and never raised:
    the rows written so far stay true, and a missing control is how a dead monitor shows."""
    record = state.store.get(STORE)
    if record is None or "rows" in record:
        return
    box, live = sandbox(AUDITOR), marked(record)
    gone, errors, rows = {}, [], []
    for machine in sorted({obj["machine"] for obj in live}):
        try:
            gone[machine] = await control(machine, live, record["nonces"]["end"])
        except Exception as ex:  # noqa: BLE001 - a machine that is down must not lose the others' controls
            errors.append(f"end control on {machine}: {ex}"[:500])
    try:
        if "pid" in record:
            await box.exec(["bash", "-c", STOP, "stop", str(record["pid"])], user="root", timeout=MONITOR_TIMEOUT_S)
    except Exception as ex:  # noqa: BLE001 - a wedged monitor must not lose the rows it wrote
        errors.append(f"stop: {ex}"[:500])
    try:
        rows = parse_rows(await box.read_file(LOG_PATH))
    except Exception as ex:  # noqa: BLE001 - no log is recorded as no rows
        errors.append(f"log: {ex}"[:500])
    state.store.set(STORE, {**record, "rows": rows, "gone": gone, "error": "; ".join(errors) or None})


@scorer(metrics=[])
def access_capture() -> Scorer:
    """The first scorer: capture, before any harness read of the honeypot. It gives no score."""
    async def score(state: TaskState, target: Target) -> None:
        await capture(state)
    return score
