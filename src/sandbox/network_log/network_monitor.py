"""The network log's API: its sidecar and the tracer's mounts, the setup solver that starts the monitor and its start
control, and the capture that brings its rows into the sample store.

    keeps_network_log(benchmark, config)                   # SwarmConfig.network_log, on a benchmark with machines
    compose = with_network_monitor(compose)                # the sidecar, with the tracer where the host allows it
    setup = [..., start_access_monitor(benchmark), start_network_monitor(benchmark)]      # src/tasks.py
    scorers = [access_capture(), network_capture(), ...]   # and capture_network(state) in Task.cleanup

The sidecar is one more compose service, NETWORK_SIDECAR: the sample's own image with the idle command, no network,
the host's pid namespace and NETWORK_CAPS (BPF and PERFMON for the tracer, SYS_PTRACE to read other containers'
/proc). It records every socket call in every machine of the sample, the agents' own and the harness's, and blocks
nothing; no tool reaches it. network_monitor_daemon.py says what it records and how: bpftrace's fexit probes when the
tracer is mounted and attaches (method ebpf), else the socket tables read every POLL_S (method procnet). It has no
SYS_ADMIN, unlike the access log's sidecar, because Inspect refuses a host bind into a service that could remount it.

The tracer. bpftrace's static release (BPFTRACE_VERSION, pinned by sha256 like scripts/box/network_trace_check.sh) is
unpacked once per Docker host into an image of its own, TRACER_IMAGE:<version>-<arch>, which the sidecar mounts at
/nix (an image mount: read only, nothing copied), with the host's /sys/kernel/tracing bound read only beside it.
ensure_tracer builds the image from the AppImage in the preflight's cache, downloading it when missing
(NETWORK_TRACE_BPFTRACE names one already on the machine), and proves it by attaching a probe in a container of the
sidecar's shape. Anything that fails leaves the sidecar without the mounts, so the run records by procnet, and a
warning says why.

The lifecycle. start_network_monitor runs after the access monitor's start, so the harness's staging comes before. It
lists the sample's containers (every compose service but the two sidecars), stores the seat table, starts the daemon,
waits for its ready row and runs the start control on every machine that holds a seat. The control (CONTROL) listens
on loopback, connects to itself and accepts, as root with the nonce in argv, and holds until the daemon's SIGUSR1; a
machine whose control leaves no connect and no accept row raises, so the sample dies before any model call. capture is
the scorer after the access log's: the end control on every machine, SIGTERM, then the log.

The store's STORE key: {"seats": as the access log's, "machines": {service: {container, pid}}, "control_machines":
[the machines with a seat], "nonces": {start, end}, "tracer": the tracer's store path in the sidecar or None, "pid":
the daemon's pid, "method": ebpf or procnet, from the ready row, and from capture "rows": the raw rows, "error"}.
"""
from __future__ import annotations

import functools
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess as host
import tempfile
import urllib.request
import warnings
from collections import Counter
from pathlib import Path

import yaml
from inspect_ai.scorer import Scorer, Target, scorer
from inspect_ai.solver import Generate, Solver, TaskState, solver
from inspect_ai.util import sandbox, subprocess

from src.sandbox import staging
from src.sandbox.access_log import access_monitor
from src.sandbox.access_log.access_monitor import AUDITOR, parse_rows, seat_record, sidecar_image
from src.sandbox.sandbox_probe import run_json

HERE = Path(__file__).parent
DAEMON = (HERE / "network_monitor_daemon.py").read_text()
STORE = "network_log"               # sample store: see the module docstring
NETWORK_SIDECAR = "netlog"          # the sidecar's compose service
NETWORK_CAPS = ["BPF", "PERFMON", "SYS_PTRACE"]
MONITOR_DIR = "/run/network_log"    # in the sidecar
DAEMON_PATH = f"{MONITOR_DIR}/network_monitor_daemon.py"
HELPER_PATH = f"{MONITOR_DIR}/access_monitor_daemon.py"     # the /proc readers the daemon imports
PLAN_PATH = f"{MONITOR_DIR}/plan.json"
LOG_PATH = f"{MONITOR_DIR}/log.jsonl"
ERRORS_PATH = f"{MONITOR_DIR}/stderr.txt"
MAX_ROWS = 50_000                   # ordinary rows past this are counted, not written
POLL_S = 0.25
MONITOR_TIMEOUT_S = 150             # bpftrace attaches in about 3 s on Colima
CONTROL_HOLD_S = 10
CONTROL_OPS = ("connect", "accept")
NETWORK_DEVIATION = "network_calls_recorded_by_a_monitor_outside_the_agents_machines"

BPFTRACE_VERSION = "v0.27.1"
BPFTRACE_SHA256 = {                 # the release's own digests, as scripts/box/network_trace_check.sh pins them
    "x86_64": "c8d570302725bd1a3ed2f23bfa8d83dd78bde95daa98856279343052db5799c9",
    "aarch64": "ad159222808d799eaa85424001535836314cf21977a71686b7b7c091b5195672",
}
BPFTRACE_URL = "https://github.com/bpftrace/bpftrace/releases/download/{version}/bpftrace-{arch}"
TRACER_IMAGE = "network-trace-bpftrace"
TRACER_LABEL = "network_trace.appimage_sha256"
TRACER_MOUNT = "/nix"
TRACEFS = "/sys/kernel/tracing"
CACHE = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "network_trace_check"
APPIMAGE_ENV = "NETWORK_TRACE_BPFTRACE"
HELPER_MEMORY = "1g"
# The tracer's image: the AppImage unpacked in a stage of `BASE` (any image with a shell: the sample's), its nix store
# alone in the image.
TRACER_DOCKERFILE = f"""ARG BASE
FROM ${{BASE}} AS unpack
COPY bpftrace.AppImage /tmp/bpftrace.AppImage
RUN cd /tmp && chmod +x bpftrace.AppImage && ./bpftrace.AppImage --appimage-extract >/dev/null
FROM scratch
COPY --from=unpack /tmp/squashfs-root/nix/store /store
ARG SHA256
LABEL {TRACER_LABEL}=$SHA256
"""
# The proof: one fexit probe attached and detached in a container of the sidecar's shape.
TRACER_CHECK = (f"{TRACER_MOUNT}/store/*-bpftrace/bin/bpftrace -e "
                "'fexit:vmlinux:inet_listen { } BEGIN { printf(\"attached\\n\"); exit(); }'")

# Start the daemon, wait for its ready row and print it; its stderr fails the wait.
START = (f"setsid -f python3 -I {DAEMON_PATH} <{PLAN_PATH} >/dev/null 2>{ERRORS_PATH}; "
         f"""until grep -qs '"row": "ready"' {LOG_PATH}; do """
         f"if [ -s {ERRORS_PATH} ]; then cat {ERRORS_PATH} >&2; exit 1; fi; sleep 0.05; done; "
         f"""grep '"row": "ready"' {LOG_PATH}""")
STOP = access_monitor.STOP
# The control: argv[1] is the nonce, argv[2] how long it waits for the daemon's SIGUSR1. Blocked first, so an early
# signal stays pending and never kills it. Printed: its port and whether the daemon answered.
CONTROL = r'''
import json, signal, socket, sys
signal.pthread_sigmask(signal.SIG_BLOCK, [signal.SIGUSR1])
server = socket.socket()
server.bind(("127.0.0.1", 0))
server.listen(1)
client = socket.create_connection(server.getsockname(), timeout=5)
peer, _ = server.accept()
acked = signal.sigtimedwait([signal.SIGUSR1], float(sys.argv[2])) is not None
print(json.dumps({"port": server.getsockname()[1], "acked": acked}))
'''


def keeps_network_log(benchmark, config) -> bool:
    """Whether a sidecar records the machines' socket calls: SwarmConfig.network_log on a benchmark whose machines the
    access log can watch (Benchmark.watched set), whether or not the access log is on."""
    return bool(getattr(config, "network_log", False)) and benchmark.watched is not None


# --------------------------------------------------------------------------
# the tracer
# --------------------------------------------------------------------------
def docker(*args: str, timeout: int = 900, **kwargs) -> host.CompletedProcess:
    return host.run(["docker", *args], capture_output=True, text=True, timeout=timeout, **kwargs)


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def appimage(arch: str) -> Path:
    """The pinned AppImage for the Docker host's arch: NETWORK_TRACE_BPFTRACE, else the preflight's cache, downloaded
    there when missing. Raises on a wrong digest."""
    expected = BPFTRACE_SHA256.get(arch)
    if expected is None:
        raise RuntimeError(f"no pinned bpftrace for {arch}")
    named = os.environ.get(APPIMAGE_ENV)
    path = Path(named) if named else CACHE / f"bpftrace-{BPFTRACE_VERSION}-{arch}"
    if not path.is_file() and not named:
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_suffix(".part")
        with urllib.request.urlopen(BPFTRACE_URL.format(version=BPFTRACE_VERSION, arch=arch), timeout=120) as r, \
                open(part, "wb") as out:
            shutil.copyfileobj(r, out)
        part.rename(path)
    found = sha256_of(path)
    if found != expected:
        raise RuntimeError(f"{path} has sha256 {found}, not the pinned {BPFTRACE_VERSION}'s")
    return path


def read_only_binds(info: dict) -> bool:
    """Whether Inspect lets a service bind a host path read only: Docker 25 or newer, Linux 5.12 or newer and a default
    runtime with recursive read-only mounts (inspect_ai's docker mounts check)."""
    engine = re.match(r"^(\d+)", info.get("ServerVersion", ""))
    kernel = re.match(r"^(\d+)\.(\d+)", info.get("KernelVersion", ""))
    runtime = (info.get("Runtimes") or {}).get(info.get("DefaultRuntime"), {})
    features = json.loads((runtime.get("status") or {}).get("org.opencontainers.runtime-spec.features", "{}"))
    return bool(engine and int(engine[1]) >= 25 and kernel and tuple(map(int, kernel.groups())) >= (5, 12)
                and "rro" in features.get("mountOptions", []))


def tracer_mounts(image: str) -> list[dict]:
    """The sidecar's mounts of the tracer's image and of tracefs, in compose's long form."""
    return [{"type": "image", "source": image, "target": TRACER_MOUNT},
            {"type": "bind", "source": TRACEFS, "target": TRACEFS, "read_only": True}]


def mount_flags(image: str) -> list[str]:
    return ["--mount", f"type=image,source={image},target={TRACER_MOUNT}", "-v", f"{TRACEFS}:{TRACEFS}:ro"]


def tracer_works(image: str, base: str) -> bool:
    """Whether a container of the sidecar's shape, `base` with the tracer's mounts and NETWORK_CAPS, attaches a
    probe."""
    caps = [flag for cap in NETWORK_CAPS for flag in ("--cap-add", cap)]
    found = docker("run", "--rm", "--network", "none", "--pid", "host", "--memory", HELPER_MEMORY, *caps,
                   *mount_flags(image), "--entrypoint", "sh", base, "-c", TRACER_CHECK, timeout=180)
    return found.returncode == 0 and "attached" in found.stdout


def build_tracer(image: str, base: str, source: Path, digest: str) -> None:
    """The tracer's image from the AppImage, unpacked in a stage of `base`."""
    with tempfile.TemporaryDirectory(prefix="network-trace-") as folder:
        context = Path(folder)
        shutil.copyfile(source, context / "bpftrace.AppImage")
        (context / "Dockerfile").write_text(TRACER_DOCKERFILE)
        built = docker("build", "-q", "--build-arg", f"BASE={base}", "--build-arg", f"SHA256={digest}", "-t", image,
                       str(context))
    if built.returncode != 0:
        raise RuntimeError(f"docker build {image}: {built.stderr.strip()[-400:]}")


@functools.cache
def ensure_tracer(base: str) -> str | None:
    """The tracer's image on this Docker host, built and proven once per process with `base` (the sidecar's image);
    None, with a warning, where it cannot be: no Docker, an unpinned arch, no download, no read-only binds, no image
    mounts, no tracefs or a kernel that refuses the probe."""
    try:
        info = json.loads(docker("info", "--format", "{{json .}}", timeout=60).stdout)
        if not read_only_binds(info):
            raise RuntimeError("Inspect allows no read-only bind of tracefs on this Docker host")
        arch = info.get("Architecture", "")
        digest = BPFTRACE_SHA256.get(arch)
        if digest is None:
            raise RuntimeError(f"no pinned bpftrace for {arch!r}")
        image = f"{TRACER_IMAGE}:{BPFTRACE_VERSION}-{arch}"
        found = docker("image", "inspect", "--format", f'{{{{index .Config.Labels "{TRACER_LABEL}"}}}}', image,
                       timeout=60)
        if found.returncode != 0 or found.stdout.strip() != digest:
            build_tracer(image, base, appimage(arch), digest)
        if tracer_works(image, base):
            return image
        raise RuntimeError(f"bpftrace in {image} attaches no probe here")
    except Exception as ex:  # noqa: BLE001 - the tables still record
        warnings.warn(f"network log: no tracer, so the socket tables record: {ex}", stacklevel=2)
        return None


# --------------------------------------------------------------------------
# the sidecar
# --------------------------------------------------------------------------
def with_network_monitor(compose: Path) -> Path:
    """A compose file beside `compose` that adds the network log's sidecar, with the tracer's mounts where
    ensure_tracer gives an image, and leaves every service as it was. Its name says which: -networked-traced.yaml
    with the tracer, -networked.yaml without. The build folder is shared, and a process with no tracer (the unit
    tests' stand-in, a host without one) must never overwrite the file a traced run is about to start from."""
    document = yaml.safe_load(compose.read_text())
    services = document["services"]
    base = sidecar_image(services)
    tracer = ensure_tracer(base)
    sidecar = staging.compose_service(working_dir="/", image=base, pid="host", cap_add=NETWORK_CAPS,
                                      **({"volumes": tracer_mounts(tracer)} if tracer else {}))
    name = f"{compose.stem}-networked{'-traced' if tracer else ''}.yaml"
    return staging.write_yaml(compose.with_name(name),
                              {**document, "services": {**services, NETWORK_SIDECAR: sidecar}})


# --------------------------------------------------------------------------
# the store's record
# --------------------------------------------------------------------------
def uncontrolled(rows: list[dict], machines: list[str], control: str) -> list[str]:
    """The machine:op pairs of CONTROL_OPS that this control left no row of, on each machine that ran it."""
    seen = Counter((row.get("machine"), row.get("op")) for row in rows
                   if row.get("row") == "net" and row.get("control") == control)
    return sorted(f"{machine}:{op}" for machine in machines for op in CONTROL_OPS if not seen[(machine, op)])


def ready_row(rows: list[dict]) -> dict | None:
    return next((row for row in rows if row.get("row") == "ready"), None)


# --------------------------------------------------------------------------
# the lifecycle
# --------------------------------------------------------------------------
async def sample_containers() -> dict[str, dict]:
    """Every compose service of the sample but the two sidecars: {service: {container, pid}}, from the host's Docker
    by the network sidecar's compose project."""
    connection = await sandbox(NETWORK_SIDECAR).connection()
    found = await subprocess(["docker", "inspect", "--format",
                              '{{index .Config.Labels "com.docker.compose.project"}}', connection.container])
    if not found.success:
        raise RuntimeError(f"docker inspect {connection.container}: {found.stderr}")
    project = found.stdout.strip()
    listed = await subprocess(["docker", "ps", "-q", "--filter", f"label=com.docker.compose.project={project}"])
    ids = listed.stdout.split()
    described = await subprocess(["docker", "inspect", "--format",
                                  '{{.Id}} {{.State.Pid}} {{index .Config.Labels "com.docker.compose.service"}}', *ids])
    if not listed.success or not described.success:
        raise RuntimeError(f"docker ps for {project}: {listed.stderr} {described.stderr}")
    out = {}
    for line in described.stdout.splitlines():
        container, pid, service = line.split()
        if service not in (AUDITOR, NETWORK_SIDECAR):
            out[service] = {"container": container, "pid": int(pid)}
    return dict(sorted(out.items()))


async def control(machine: str, nonce: str) -> dict:
    """The control on one machine: its port and whether the daemon answered."""
    return await run_json(CONTROL, args=[nonce, str(CONTROL_HOLD_S)], box=sandbox(machine),
                          timeout=CONTROL_HOLD_S + 30)


@solver
def start_network_monitor(benchmark) -> Solver:
    """Task setup, after the access monitor's: store the record, start the monitor over every machine and run the
    start control. Any failure raises, so a run with no network log spends nothing."""
    async def solve(state: TaskState, generate: Generate) -> TaskState:
        nonces = {"start": secrets.token_hex(8), "end": secrets.token_hex(8)}
        machines = await sample_containers()
        seats = seat_record(benchmark, state)
        held = sorted({seat["machine"] for seat in seats if not seat.get("observer")} & set(machines))
        box = sandbox(NETWORK_SIDECAR)
        tracer = (await box.exec(["sh", "-c", f"ls -d {TRACER_MOUNT}/store/*-bpftrace 2>/dev/null"])).stdout.strip()
        record = {"seats": seats, "machines": machines, "control_machines": held, "nonces": nonces,
                  "tracer": tracer or None}
        state.store.set(STORE, record)
        plan = {"log": LOG_PATH, "max_rows": MAX_ROWS, "poll_s": POLL_S, "method": "auto", "nonces": nonces,
                "machines": machines}
        await box.write_file(HELPER_PATH, access_monitor.DAEMON)
        await box.write_file(DAEMON_PATH, DAEMON)
        await box.write_file(PLAN_PATH, json.dumps(plan))
        started = await box.exec(["bash", "-c", START], user="root", timeout=MONITOR_TIMEOUT_S)
        if not started.success:
            raise RuntimeError(f"network monitor did not start: {started.stderr[-800:]}")
        ready = json.loads(started.stdout.strip().splitlines()[-1])
        state.store.set(STORE, {**record, "pid": ready["pid"], "method": ready["method"]})
        for machine in held:
            await control(machine, nonces["start"])
        missing = uncontrolled(parse_rows(await box.read_file(LOG_PATH)), held, "start")
        if missing:
            raise RuntimeError(f"network monitor: the start control left no row for {missing}")
        return state
    return solve


async def capture_network(state: TaskState) -> None:
    """The end control, SIGTERM and the log, into the store, once per sample. What fails is recorded and never raised:
    the rows written so far stay true, and a missing control is how a dead monitor shows."""
    record = state.store.get(STORE)
    if record is None or "rows" in record:
        return
    box, errors, rows = sandbox(NETWORK_SIDECAR), [], []
    for machine in record.get("control_machines", []):
        try:
            await control(machine, record["nonces"]["end"])
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
    state.store.set(STORE, {**record, "rows": rows, "error": "; ".join(errors) or None})


@scorer(metrics=[])
def network_capture() -> Scorer:
    """The scorer after the access log's capture: capture_network. It gives no score."""
    async def score(state: TaskState, target: Target) -> None:
        await capture_network(state)
    return score
