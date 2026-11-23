"""What /proc shows an agent: only its own processes, and no page map (SwarmConfig.proc_hiding, on by default).

    keeps_proc_hiding(benchmark, config)        # the switch, on a benchmark whose agents hold a shell
    compose = with_proc_hiding(compose)         # every service under PROFILE, loaded into the Docker host first
    setup = [..., hide_proc(benchmark), benchmark.setup(), ...]      # src/tasks.py, before any agent command

Two protections, each proven in every run on its own Docker host before any agent runs.

Processes. Each agent machine's /proc is remounted with hidepid=2 (the kernel shows it as hidepid=invisible) and
gid=PROC_GID: a process sees another's /proc/<pid> only when it may ptrace it, which in a container without
SYS_PTRACE means only its own uid's, or when it holds PROC_GID. Where agents share a machine (openmath's split and solo
layouts, knowledge work and creative writing), an agent's `ps`, a listing of /proc and a read of
/proc/<pid>/cmdline or environ no longer show a teammate's commands, nor the harness's own root processes (the
controls' nonces, the job collectors). An agent's own processes stay whole: ps, top, Python and bash's <(...), which
reads /proc/self/fd. The group PROC_GROUP (gid PROC_GID) holds root in each machine's /etc/group, so every exec of ours
as root (`--user root`: the reaps, the end state, the command jobs' launcher and readers, the controls) sees every
process as before; an exec with no user gets the groups the container started with and sees only root's. A peer_shell
observer runs with PROC_GID as its group (src/sandbox/peer_shell.py), so it sees the watched peer's processes, and
every other process of that machine, as before. Holding PROC_GID, the observer may leave a setgid-PROC_GID file where
anyone can write (/tmp); so every service runs with Docker's no-new-privileges, which the kernel keeps on every
process of the container, each exec included, and no exec of a setgid or setuid file changes an agent's ids (sudo
and su stop working for an agent). Agents that share one uid (openmath's shared layout, CheatBench's `user` for every
agent) still see each other: the record names those machines. The access log's and the network log's
sidecars read /proc in the host's pid namespace, which the remount does not touch. Docker gives a container no way to
mount /proc with options, and its processes lack SYS_ADMIN, so a privileged helper container in the host's pid
namespace enters each machine's mount namespace and remounts it (host_helper.py).

Page maps. /proc/<pid>/pagemap reads 8 bytes per page of the whole virtual address space, about 512 GB on aarch64 and
256 GB on x86_64 at about 1 GB/s, so a whole-disk search (`grep -r PATTERN /`) read its own page map for about 400 s
and never ended inside a round. No mount option hides one file of each pid, and a process may always read its own,
so every service of the sample runs under an AppArmor profile of its own, PROFILE: Docker's docker-default
(moby/profiles apparmor v0.2.1, the version Docker 29.5 vendors) with one rule more, `deny @{PROC}/**/pagemap`. Every
service gets it, the sidecars and the mirror included, because AppArmor's ptrace rules name the peer's profile:
a sidecar under docker-default could no longer read an agent's /proc/<pid>/fd. The helper loads it into the Docker
host's kernel with the host's apparmor_parser, as Docker loads docker-default, once per process; its name carries the
digest of its text. A host whose Docker has no AppArmor refuses the run.

What stays. `grep -R /` (which follows symlinks) still does not end in a round: it walks /sys's symlinks over and over
and blocks reading a pipe through /proc/<pid>/fd. Only `grep -r` and its kind end. Root in a machine (SWE's agents)
can undo none of it, but sees every process there, as before; a machine whose init it kills comes back with a fresh
/proc that hides nothing (the profile stays). The group's line in /etc/group and the mount's options
in /proc/mounts are visible to an agent.

The proof (proc_proof.py, as root in each machine): a holder process of one throwaway uid and a viewer of another;
the viewer must see no pid of another uid, neither the holder nor root's init nor its parent, and must fail to open
its own page map with EACCES, while its /proc/self, /proc/self/fd and /dev/fd work; run through a setgid-PROC_GID
copy of python3 in /tmp, it must keep its own egid, see no other uid's pid and hold no-new-privileges; root must see
the holder and fail on its own page map too. Any failure raises, so the sample dies before any model call.

The sample store's STORE: {status "on", proc_hidepid, pagemap_hidden, profile, gid, kernel, shared_uid: {machine:
[agents sharing one uid there]}, machines: {machine: {proc, profile, group, report}}}; run.json sandbox.proc carries
it, or OFF for a run without it (every run before 2026-11-22).
"""
from __future__ import annotations

import functools
import hashlib
import json
import subprocess as host
from pathlib import Path

import yaml
from inspect_ai.solver import Generate, Solver, TaskState, solver
from inspect_ai.util import sandbox, subprocess

from src.sandbox import staging
from src.sandbox.access_log.access_monitor import container_of
from src.sandbox.agent_tools import Seat
from src.sandbox.machines import agent_machines as machines
from src.sandbox.sandbox_probe import run_json

HERE = Path(__file__).parent
TEMPLATE = (HERE / "agent_profile.apparmor").read_text()
HOST_HELPER = (HERE / "host_helper.py").read_text()
PROC_GROUP_SCRIPT = (HERE / "proc_group.py").read_text()
PROOF = (HERE / "proc_proof.py").read_text()
PROFILE = f"swarm-agent-{hashlib.sha256(TEMPLATE.encode()).hexdigest()[:12]}"
PROC_GROUP = "procview"
PROC_GID = 2999                 # below the observers (from 3001), above the agent users (from 1001)
REMOUNT_DATA = f"hidepid=2,gid={PROC_GID}"
PROOF_UIDS = [2997, 2998]       # the proof's holder and viewer: no agent, observer or image user has them
STORE = "proc_hiding"           # sample store: see the module docstring
HELPER_IMAGE = machines.IMAGE   # python3 for the helper; built by ensure_profile
HELPER_MEMORY = "256m"
HELPER_TIMEOUT_S = 120
PROC_DEVIATION = "proc_shows_each_agent_only_its_own_processes_and_no_page_map"
OFF = {"status": "off", "proc_hidepid": False, "pagemap_hidden": False}
REFUSAL = "--set proc_hiding=false runs without it"


def keeps_proc_hiding(benchmark, config) -> bool:
    """Whether the run hides /proc: SwarmConfig.proc_hiding on a benchmark whose agents hold a shell (chess holds
    none)."""
    return bool(getattr(config, "proc_hiding", False)) and bool(getattr(benchmark, "commands", False))


# --------------------------------------------------------------------------
# the profile and the compose file
# --------------------------------------------------------------------------
def helper_argv(image: str = HELPER_IMAGE) -> list[str]:
    """The privileged helper's container: the host's pid namespace, no network, host_helper.py on python3."""
    return ["docker", "run", "--rm", "-i", "--privileged", "--pid", "host", "--network", "none",
            "--memory", HELPER_MEMORY, "--label", "proc_hiding=1", "--entrypoint", "python3", image,
            "-I", "-c", HOST_HELPER]


def apparmor_enabled(info: dict) -> bool:
    """Whether the Docker host confines containers with AppArmor (docker info's SecurityOptions)."""
    return any(str(option).startswith("name=apparmor") for option in info.get("SecurityOptions") or [])


@functools.cache
def ensure_profile() -> str:
    """PROFILE loaded into the Docker host's kernel, once per process; raises where it cannot be (no Docker, no
    AppArmor, no apparmor_parser on the host, a profile that does not parse)."""
    found = host.run(["docker", "info", "--format", "{{json .}}"], capture_output=True, text=True, timeout=60)
    if found.returncode != 0:
        raise RuntimeError(f"proc hiding: docker info: {found.stderr.strip()[-300:]}")
    if not apparmor_enabled(json.loads(found.stdout)):
        raise RuntimeError(f"proc hiding: this Docker host has no AppArmor, so no page map can be hidden; {REFUSAL}")
    machines.ensure_image()
    loaded = host.run(helper_argv(), input=json.dumps({"action": "profile", "template": TEMPLATE, "name": PROFILE}),
                      capture_output=True, text=True, timeout=HELPER_TIMEOUT_S)
    if loaded.returncode != 0:
        raise RuntimeError(f"proc hiding: the profile did not load: {loaded.stderr.strip()[-600:]}; {REFUSAL}")
    return json.loads(loaded.stdout.strip().splitlines()[-1])["name"]


def security_options(profile: str) -> list[str]:
    """The security options of every service: no setgid or setuid file takes effect, and the profile."""
    return ["no-new-privileges:true", f"apparmor={profile}"]


def under_profile(services: dict[str, dict], profile: str) -> dict[str, dict]:
    """Every service with security_options(profile) in its security_opt, its other options kept."""
    out = {}
    for name, service in services.items():
        options = [o for o in service.get("security_opt") or []
                   if not str(o).startswith(("apparmor", "no-new-privileges"))]
        out[name] = {**service, "security_opt": [*options, *security_options(profile)]}
    return out


def with_proc_hiding(compose: Path) -> Path:
    """A compose file beside `compose`, <stem>-hidden.yaml, with every service under PROFILE, loaded first."""
    document = yaml.safe_load(compose.read_text())
    profile = ensure_profile()
    return staging.write_yaml(compose.with_name(f"{compose.stem}-hidden.yaml"),
                              {**document, "services": under_profile(document["services"], profile)})


# --------------------------------------------------------------------------
# the proof's reading
# --------------------------------------------------------------------------
def hides_pids(options: str) -> bool:
    """Whether /proc's superblock options hide other users' pids from all but PROC_GID."""
    parts = options.split(",")
    return bool({"hidepid=invisible", "hidepid=2"} & set(parts)) and f"gid={PROC_GID}" in parts


def proof_failures(report: dict) -> dict[str, list[str]]:
    """What the proof of one machine found wrong, by protection: {"hidepid": [...], "pagemap": [...]}."""
    viewer, root = report.get("viewer") or {}, report.get("root") or {}
    hidepid, pagemap = [], []
    if not hides_pids(report.get("proc", "")):
        hidepid.append(f"/proc is mounted {report.get('proc')!r}")
    if viewer.get("other_owners"):
        hidepid.append(f"an agent uid sees processes of uids {viewer['other_owners']}")
    for key, what in (("sees_holder", "another uid's process"), ("sees_init", "root's init"),
                      ("sees_parent", "the root process that started it")):
        if viewer.get(key, True):
            hidepid.append(f"an agent uid sees {what}")
    if viewer.get("holder_cmdline") != "ENOENT":
        hidepid.append(f"an agent uid opens another's cmdline: {viewer.get('holder_cmdline')}")
    for key in ("self", "self_fd", "dev_fd"):
        if viewer.get(key) != "ok":
            hidepid.append(f"an agent uid loses its own /proc ({key}: {viewer.get(key)})")
    setgid = report.get("setgid") or {}
    if setgid.get("egid") in (None, PROC_GID) or setgid.get("other_owners") != [] or setgid.get("no_new_privs") != "1":
        hidepid.append(f"an agent uid that runs a setgid-{PROC_GROUP} file sees other processes: {setgid}")
    if not root.get("sees_holder") or root.get("holder_cmdline") != "ok":
        hidepid.append(f"root does not see an agent's process: {root}")
    if report.get("profile") != PROFILE:
        pagemap.append(f"the machine runs under {report.get('profile')!r}, not {PROFILE}")
    for who, key in (("an agent uid", viewer.get("pagemap")), ("an agent uid's thread", viewer.get("task_pagemap")),
                     ("root", root.get("pagemap"))):
        if key != "EACCES":
            pagemap.append(f"{who} opens its page map: {key}")
    return {"hidepid": hidepid, "pagemap": pagemap}


def machines_of(seats: dict[str, Seat]) -> dict[str, list[str]]:
    """{service: its agents}, in order."""
    out: dict[str, list[str]] = {}
    for agent, seat in seats.items():
        out.setdefault(seat.service or machines.DEFAULT_SERVICE, []).append(agent)
    return out


def shared_uids(seats: dict[str, Seat]) -> dict[str, list[str]]:
    """{service: the agents that work there as one Linux user}: hidepid cannot part those."""
    out: dict[str, list[str]] = {}
    for service, agents in machines_of(seats).items():
        by_user: dict[str | None, list[str]] = {}
        for agent in agents:
            by_user.setdefault(seats[agent].user, []).append(agent)
        same = [a for group in by_user.values() if len(group) > 1 for a in group]
        if same:
            out[service] = same
    return out


def record(seats: dict[str, Seat], groups: dict[str, dict], remounted: dict[str, dict],
           reports: dict[str, dict]) -> dict:
    """The store's STORE from the setup's steps; raises RuntimeError when any machine's proof failed."""
    failures = {service: proof_failures(report) for service, report in reports.items()}
    wrong = [f"{service}: {msg}" for service, found in failures.items() for msg in found["hidepid"] + found["pagemap"]]
    if wrong:
        raise RuntimeError(f"proc hiding: {'; '.join(wrong)[:1500]}; {REFUSAL}")
    kernel = next((r.get("kernel") for r in reports.values() if r.get("kernel")), None)
    return {"status": "on", "proc_hidepid": True, "pagemap_hidden": True, "profile": PROFILE, "gid": PROC_GID,
            "kernel": kernel, "shared_uid": shared_uids(seats),
            "machines": {service: {"proc": (remounted.get(service) or {}).get("super"),
                                   "profile": reports[service].get("profile"),
                                   "group": (groups.get(service) or {}).get("group"),
                                   "report": reports[service]} for service in reports}}


# --------------------------------------------------------------------------
# the setup
# --------------------------------------------------------------------------
async def remount(pids: dict[str, int]) -> dict[str, dict]:
    """The helper's remount of each container's /proc ({service: host pid}): {service: its /proc line after}."""
    order = {"action": "remount", "pids": pids, "data": REMOUNT_DATA}
    done = await subprocess(helper_argv(), input=json.dumps(order), timeout=HELPER_TIMEOUT_S)
    if not done.success:
        raise RuntimeError(f"proc hiding: the remount failed: {done.stderr.strip()[-600:]}; {REFUSAL}")
    return json.loads(done.stdout.strip().splitlines()[-1])


@solver
def hide_proc(benchmark) -> Solver:
    """Task setup, before the benchmark's: on every machine that holds a seat, root joins PROC_GROUP, /proc is
    remounted with REMOUNT_DATA and the proof runs; the record goes to the store's STORE. Raises on any failure."""
    async def solve(state: TaskState, generate: Generate) -> TaskState:
        seats = benchmark.seats(state)
        services = list(machines_of(seats))
        groups = {service: await run_json(PROC_GROUP_SCRIPT, stdin={"name": PROC_GROUP, "gid": PROC_GID},
                                          box=sandbox(service)) for service in services}
        pids = {service: (await container_of(service))["pid"] for service in services}
        remounted = await remount(pids)
        reports = {service: await run_json(PROOF, stdin={"gid": PROC_GID, "uids": PROOF_UIDS}, box=sandbox(service))
                   for service in services}
        state.store.set(STORE, record(seats, groups, remounted, reports))
        return state
    return solve
