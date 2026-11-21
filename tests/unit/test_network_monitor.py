"""The network log's monitor (src/sandbox/network_log/): the tracer's script and pins, how the daemon reads a kernel
event and the socket tables, the control's rows, the sidecar's mounts and the setup's checks. No docker."""
from __future__ import annotations

import ast
import errno
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from src.configuration.presets import build_config
from src.configuration.settings import ROOT
from src.domains.chess import Chess
from src.domains.interview import SubsetSum
from src.sandbox.access_log import access_monitor
from src.sandbox.network_log import network_monitor as monitor
from src.sandbox.network_log import network_monitor_daemon as daemon
from src.sandbox.network_log.network_monitor import NETWORK_SIDECAR

IMAGE = "cbtask:python311"
NS = 4026532448
ENSURE_TRACER = monitor.ensure_tracer       # before tests/unit/conftest.py's no_tracer replaces it


class Lines:
    """A stream that keeps what RowLog writes."""

    def __init__(self) -> None:
        self.text = ""

    def write(self, text: str) -> None:
        self.text += text

    def rows(self) -> list[dict]:
        return [json.loads(line) for line in self.text.splitlines()]


def fake_monitor(**fields) -> tuple[daemon.Monitor, Lines]:
    """A Monitor over one machine k7q2m in NS, its /proc readers replaced by `fields`' who."""
    lines = Lines()
    m = daemon.Monitor.__new__(daemon.Monitor)
    m.log, m.poll_s, m.nonces = daemon.RowLog(lines, 100), 0.25, {"start": b"n0nce"}
    m.machines = {"k7q2m": {"container": "c1", "pid": 10, "down": False, "netns": NS}}
    m.netns, m.execs, m.acked, m.known = {NS: ["k7q2m"]}, {}, set(), {}
    who = fields.get("who", {"uid": 1001, "exe": "/usr/bin/python3", "cmd": "python3 -c x", "chain": [], "root": {}})
    m.who = lambda pid, parents=(): dict(who) if pid not in m.execs else {"gone": True, "exe": m.execs[pid],
                                                                         "exe_from": "exec"}
    m.machine_of = lambda pid, netns: "k7q2m" if netns == NS else None
    return m, lines


# --------------------------------------------------------------------------
# what runs in the sidecar
# --------------------------------------------------------------------------
@pytest.mark.parametrize("script", [monitor.DAEMON, monitor.CONTROL])
def test_what_runs_in_a_container_is_standard_python(script):
    ast.parse(script)


def test_the_script_filters_to_the_machines_namespaces():
    """The caller's namespace on every socket call and exec, the socket's own on the state tracepoint (a handshake
    settles in a softirq), and the kernel's parent and grandparent in every call's print."""
    script = daemon.trace_script([4026532511, NS])
    test = "(curtask->nsproxy->net_ns->ns.inum == 4026532448 || curtask->nsproxy->net_ns->ns.inum == 4026532511)"
    sk = f"({daemon.SOCKET_NETNS} == 4026532448 || {daemon.SOCKET_NETNS} == 4026532511)"
    assert "FILTER" not in script and "PARENTS" not in script
    assert daemon.TRACE.count("SKFILTER") == 1 and script.count(sk) == 1
    assert script.count(test) == daemon.TRACE.count("FILTER") - daemon.TRACE.count("SKFILTER") == 13
    assert script.count(daemon.PARENTS) == daemon.TRACE.count("PARENTS") == 13
    probes = re.findall(r"^(f(?:exit|entry):vmlinux:\w+|tracepoint:\S+)", script, re.M)
    assert {p.split(":")[-1] for p in probes} >= {"inet_bind", "inet_listen", "inet_stream_connect", "inet_csk_accept",
                                                  "udp_sendmsg", "inet_release", "unix_bind", "unix_listen",
                                                  "unix_stream_connect", "unix_accept", "security_bprm_check",
                                                  "inet_sock_set_state"}
    assert "tracepoint:sock:inet_sock_set_state /args.protocol == 6 && args.oldstate == 2 && " in script


PREFLIGHT = ROOT / "scripts" / "box" / "network_trace_check.sh"


def test_the_pins_are_the_preflights():
    """The run and the preflight (scripts/box/network_trace_check.sh) trust the same release by the same digests."""
    text = PREFLIGHT.read_text()
    assert f"BPFTRACE_VERSION={monitor.BPFTRACE_VERSION}" in text
    for arch, digest in monitor.BPFTRACE_SHA256.items():
        assert re.search(rf"{arch}\) echo {digest} ;;", text), arch


def test_the_preflights_helper_is_the_sidecar():
    """The preflight's helper has the sidecar's shape: its capabilities and no SYS_ADMIN, the host's pid namespace, no
    network, an init, the tracer's image under its name and label at the sidecar's mount, tracefs read only, and the
    image built from the run's own Dockerfile; it runs the binary the daemon globs."""
    text = PREFLIGHT.read_text()
    [caps] = re.findall(r"^SIDECAR_CAPS=\((.*)\)$", text, re.M)
    assert re.findall(r"--cap-add (\S+)", caps) == monitor.NETWORK_CAPS
    assert "--cap-add SYS_ADMIN" not in text and "SYS_ADMIN" not in caps
    [args] = re.findall(r"^helper_args=\((.*)\)$", text, re.M)
    words = args.split()
    assert "--init" in words and {("--network", "none"), ("--pid", "host")} <= set(zip(words, words[1:]))
    assert monitor.staging.compose_service(working_dir="/")["init"] is True
    assert f'--mount "type=image,source=$tracer_image,target={monitor.TRACER_MOUNT}"' in text
    assert f"-v {monitor.TRACEFS}:{monitor.TRACEFS}:ro" in text
    assert f"TRACER_IMAGE={monitor.TRACER_IMAGE}:$BPFTRACE_VERSION-$arch" in text
    assert f"TRACER_LABEL={monitor.TRACER_LABEL}" in text
    [dockerfile] = re.findall(r"<<'DOCKERFILE'\n(.*?)^DOCKERFILE$", text, re.M | re.S)
    assert dockerfile == monitor.TRACER_DOCKERFILE
    assert f"ls -d {daemon.TRACER_GLOB} " in text
    assert "tracepoint:sock:inet_sock_set_state" in text


def test_the_daemon_finds_its_helper_beside_it_under_isolated_mode(tmp_path):
    """python3 -I keeps the script's folder off sys.path; the daemon puts it back, so the /proc readers import. Here
    the plan names a machine with no namespace, which is the first thing the monitor needs."""
    (tmp_path / "network_monitor_daemon.py").write_text(monitor.DAEMON)
    (tmp_path / "access_monitor_daemon.py").write_text(access_monitor.DAEMON)
    plan = {"log": str(tmp_path / "log.jsonl"), "max_rows": 10, "nonces": {}, "machines": {"m": {"container": "c",
                                                                                               "pid": 999999999}}}
    done = subprocess.run([sys.executable, "-I", str(tmp_path / "network_monitor_daemon.py")], input=json.dumps(plan),
                          capture_output=True, text=True)
    assert done.returncode != 0 and "no network namespace for m" in done.stderr, done.stderr
    assert "ModuleNotFoundError" not in done.stderr


# --------------------------------------------------------------------------
# a kernel event
# --------------------------------------------------------------------------
def event(*data) -> str:
    return json.dumps({"type": "value", "data": list(data)})


SK = "18446576088245340480"           # bpftrace prints a kernel address as a string
PARENTS = [60, 50]


@pytest.mark.parametrize(("data", "want"), [
    (["connect", 5_000_000_000, 77, 77, 3001, NS, 1, 2, "127.0.0.1", 46152, "127.0.0.1", 8123, -errno.EINPROGRESS,
      "python3", SK, *PARENTS],
     {"op": "connect", "proto": "tcp", "family": "inet", "type": "stream", "laddr": "127.0.0.1", "lport": 46152,
      "raddr": "127.0.0.1", "rport": 8123, "result": "EINPROGRESS", "kernel_uid": 3001, "sk": int(SK),
      "parents": PARENTS}),
    (["listen", 1, 70, 70, 1001, NS, 1 | 0x800, 10, "::ffff:127.0.0.1", 8123, "", 0, 0, "srv", SK, *PARENTS],
     {"op": "listen", "proto": "tcp", "family": "inet6", "laddr": "127.0.0.1", "lport": 8123, "raddr": None,
      "rport": None, "result": "ok"}),
    (["send", 1, 71, 71, 3001, NS, 2, 2, "0.0.0.0", 56000, "127.0.0.1", 9999, 1, "py", SK, *PARENTS],
     {"op": "send", "proto": "udp", "type": "dgram", "rport": 9999, "result": "ok"}),
    (["unix_bind", 1, 72, 72, 1001, NS, 1, 1, "abstract-name", 1, "", 0, 0, "py", SK, *PARENTS],
     {"op": "bind", "proto": "unix", "laddr": "@abstract-name", "lport": None, "raddr": None}),
    (["unix_connect", 1, 73, 73, 3001, NS, 1, 1, "", 0, "/tmp/srv.sock", 0, -errno.EACCES, "py", SK, *PARENTS],
     {"op": "connect", "proto": "unix", "laddr": None, "raddr": "/tmp/srv.sock", "result": "EACCES"}),
])
def test_a_kernel_event_reads_as_a_row(data, want):
    fields = daemon.kernel_event(data)
    assert {key: fields[key] for key in want} == want


def test_an_event_becomes_a_net_row_with_the_kernels_time_and_an_exec_names_a_gone_process():
    m, lines = fake_monitor()
    m.event(event("connect", 2_500_000_000, 77, 77, 3001, NS, 1, 2, "127.0.0.1", 46152, "127.0.0.1", 8123, 0, "py",
                  SK, *PARENTS), clock=1000.0)
    m.event(event("exec", 1, 78, 78, 1001, NS, 0, 0, "/usr/bin/nc", 0, "", 0, 0, "nc", "0", *PARENTS), clock=1000.0)
    m.event(event("listen", 3_000_000_000, 78, 78, 1001, NS, 1, 2, "0.0.0.0", 9000, "", 0, 0, "nc", SK, *PARENTS),
            clock=1000.0)
    m.event(json.dumps({"type": "lost_events", "data": {"events": 3}}), clock=1000.0)
    connect, listen, lost = lines.rows()
    assert (connect["row"], connect["op"], connect["machine"], connect["uid"], connect["t_kernel"]) == \
        ("net", "connect", "k7q2m", 1001, 1002.5) and connect["source"] == "ebpf" and connect["pid"] == 77
    assert connect["sk"] == int(SK) and "parents" not in connect
    assert listen["gone"] and listen["exe"] == "/usr/bin/nc" and listen["uid"] == 1001 and listen["comm"] == "nc"
    assert lost == {**lost, "row": "lost", "events": 3} and m.log.lost == 3


def test_a_socket_leaving_syn_sent_is_a_tcp_state_row_on_the_sockets_machine():
    """The state print fires where the kernel settles the handshake: its netns names the machine, and the error the
    kernel set on a refused connect comes as its name."""
    m, lines = fake_monitor()
    m.event(event("state", 4_000_000_000, 0, 0, 0, NS, 2, 7, "", 37074, "", 8301, errno.ECONNREFUSED, "", SK, 0, 0),
            clock=1000.0)
    m.event(event("state", 4_100_000_000, 0, 0, 0, NS, 2, 1, "", 43036, "", 8205, 0, "", SK, 0, 0), clock=1000.0)
    refused, established = lines.rows()
    assert {k: refused[k] for k in ("row", "netns", "machine", "sk", "lport", "rport", "old", "new", "error",
                                    "source", "t_kernel")} == \
        {"row": "tcp_state", "netns": NS, "machine": "k7q2m", "sk": int(SK), "lport": 37074, "rport": 8301,
         "old": "SYN_SENT", "new": "CLOSE", "error": "ECONNREFUSED", "source": "ebpf", "t_kernel": 1004.0}
    assert (established["new"], established["error"]) == ("ESTABLISHED", None)


def test_a_process_gone_first_is_named_through_its_nearest_ancestor_still_there(monkeypatch):
    """bash's /dev/tcp child exits before /proc is read: its row takes its parent's chain and root, with via, so its
    origin and call follow; with every ancestor gone it reads gone."""
    m, _ = fake_monitor()
    del m.who
    alive = {60: {"uid": 1001, "ppid": 50, "ns_pid": 7}, 50: {"uid": 1001, "ppid": 1, "ns_pid": 2}}
    monkeypatch.setattr(daemon, "status", lambda pid: alive.get(int(pid)))
    monkeypatch.setattr(daemon, "ancestry", lambda pid, found: ([f"argv of {pid}"], {"pid": 50, "cmd": "bash -c x"}))
    m.execs[77] = "/usr/bin/bash"
    named = m.who(77, [60, 50])
    assert named == {"exe": "/usr/bin/bash", "exe_from": "exec", "cwd": None, "cmd": None,
                     "chain": ["argv of 60"], "root": {"pid": 50, "cmd": "bash -c x"}, "via": 60}
    assert m.who(77, [99, 50])["via"] == 50
    assert m.who(77, [99, 98]) == {"gone": True, "exe": "/usr/bin/bash", "exe_from": "exec"}
    assert m.who(77, [1, 0]) == {"gone": True, "exe": "/usr/bin/bash", "exe_from": "exec"}


def test_past_the_cap_ordinary_rows_are_counted_and_a_controls_rows_still_pass():
    lines = Lines()
    log = daemon.RowLog(lines, 2)
    for _ in range(3):
        log.write("net", op="connect")
    log.write("net", op="accept", control="end")
    log.write("stopped", rows=log.seen, dropped=log.dropped)
    rows = lines.rows()
    assert [r["row"] for r in rows] == ["net", "net", "net", "stopped"] and rows[2]["control"] == "end"
    assert log.dropped == 1 and rows[-1]["dropped"] == 1


# --------------------------------------------------------------------------
# the socket tables
# --------------------------------------------------------------------------
# The tables as the kernel writes them, the columns past inode cut.
TCP = """  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode
   0: 0100007F:1FBB 00000000:0000 0A 00000000:00000000 00:00000000 00000000  1001        0 5001
   1: 0100007F:B448 0100007F:1FBB 01 00000000:00000000 00:00000000 00000000  3001        0 5002
   2: 0100007F:1FBB 0100007F:B448 01 00000000:00000000 00:00000000 00000000  1001        0 5003
   3: 0100007F:2328 00000000:0000 07 00000000:00000000 00:00000000 00000000  1001        0 5004
"""
V6_ANY, V6_LOOPBACK = "0" * 32, "0000000000000000FFFF00000100007F"
TCP6 = f"""  sl  local_address remote_address st tx_queue rx_queue tr tm->when retrnsmt uid timeout inode
   0: {V6_ANY}:1F90 {V6_ANY}:0000 0A 00000000:00000000 00:00000000 00000000 1001 0 6001
   1: {V6_LOOPBACK}:1F91 {V6_ANY}:0000 0A 00000000:00000000 00:00000000 00000000 1001 0 6002
"""
UDP = """   sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode
  0: 00000000:270F 00000000:0000 07 00000000:00000000 00:00000000 00000000  1001        0 7001
  1: 0100007F:DAC0 0100007F:270F 01 00000000:00000000 00:00000000 00000000  3001        0 7002
"""
UNIX = """Num       RefCount Protocol Flags    Type St Inode Path
0000000000000000: 00000002 00000000 00010000 0001 01 8001 /tmp/srv.sock
0000000000000000: 00000003 00000000 00000000 0001 03 8002 /tmp/srv.sock
0000000000000000: 00000002 00000000 00000000 0002 01 8003 @abstract name
"""


def test_the_inet_tables_read_addresses_ports_owners_and_states():
    tcp = daemon.inet_table(TCP, "tcp")
    assert [(s["laddr"], s["lport"], s["raddr"], s["rport"], s["state"], s["table_uid"], s["inode"]) for s in tcp] == [
        ("127.0.0.1", 8123, "0.0.0.0", 0, "0A", 1001, 5001), ("127.0.0.1", 46152, "127.0.0.1", 8123, "01", 3001, 5002),
        ("127.0.0.1", 8123, "127.0.0.1", 46152, "01", 1001, 5003), ("127.0.0.1", 9000, "0.0.0.0", 0, "07", 1001, 5004)]
    tcp6 = daemon.inet_table(TCP6, "tcp6")
    assert [(s["family"], s["laddr"], s["lport"]) for s in tcp6] == [("inet6", "::", 8080),
                                                                       ("inet6", "127.0.0.1", 8081)]
    assert daemon.inet_table(UDP, "udp")[1]["proto"] == "udp"


def test_each_socket_reads_as_one_op():
    socks = daemon.inet_table(TCP, "tcp") + daemon.inet_table(UDP, "udp") + daemon.unix_table(UNIX)
    listening = {s["lport"] for s in socks if s["proto"] == "tcp" and s["state"] == daemon.TCP_LISTEN}
    ops = [(s["proto"], daemon.classify(s, listening)) for s in socks]
    assert ops == [("tcp", "listen"), ("tcp", "connect"), ("tcp", "accept"), ("tcp", "bind"), ("udp", "bind"),
                   ("udp", "connect"), ("unix", "listen"), ("unix", "accept"), ("unix", "bind")]
    assert daemon.unix_table(UNIX)[2]["laddr"] == "@abstract name"


def test_a_read_of_the_tables_gives_a_row_per_new_socket_and_a_close_per_socket_gone():
    m, lines = fake_monitor()
    first = {(NS, 5001): {"fields": {"op": "listen", "netns": NS, "lport": 8123}, "pid": 70}}
    second = {(NS, 5001): {"fields": {"op": "listen", "netns": NS, "lport": 8123}, "pid": 70},
              (NS, 5002): {"fields": {"op": "connect", "netns": NS, "lport": 46152}, "pid": 77}}
    snapshots = iter([first, second, {}])
    m.snapshot = lambda: next(snapshots)
    m.table_rows(at_start=True)
    m.table_rows(at_start=False)
    m.table_rows(at_start=False)
    rows = lines.rows()
    assert [(r["op"], r["pid"], r.get("at_start", False)) for r in rows] == \
        [("listen", 70, True), ("connect", 77, False), ("close", 70, False), ("close", 77, False)]
    assert all(r["source"] == "procnet" and r["uid"] == 1001 and r["machine"] == "k7q2m" for r in rows)


def test_a_read_that_finds_a_connect_out_of_syn_sent_writes_a_tcp_state_row():
    """procnet: a connect seen in SYN_SENT, then ESTABLISHED, gives one tcp_state row; one seen in SYN_SENT and then
    gone gives CLOSE with no error."""
    m, lines = fake_monitor()

    def connect(inode, lport, state):
        return {(NS, inode): {"fields": {"op": "connect", "netns": NS, "socket": inode, "lport": lport, "rport": 8180,
                                         "tcp_state": state}, "pid": 77}}
    snapshots = iter([{**connect(1, 40001, "SYN_SENT"), **connect(2, 40002, "SYN_SENT")},
                      {**connect(1, 40001, "ESTABLISHED"), **connect(2, 40002, "SYN_SENT")}, connect(1, 40001,
                                                                                                   "ESTABLISHED")])
    m.snapshot = lambda: next(snapshots)
    for at_start in (True, False, False):
        m.table_rows(at_start=at_start)
    states = [r for r in lines.rows() if r["row"] == "tcp_state"]
    assert [(r["socket"], r["lport"], r["new"], r["error"], r["source"]) for r in states] == \
        [(1, 40001, "ESTABLISHED", None, "procnet"), (2, 40002, "CLOSE", None, "procnet")]
    assert daemon.TCP_STATES[int("02", 16)] == "SYN_SENT"


def test_a_controls_row_sends_it_the_signal_once(monkeypatch):
    m, _ = fake_monitor()
    del m.who
    sent = []
    monkeypatch.setattr(daemon, "status", lambda pid: {"uid": 0, "ppid": 1, "ns_pid": 9})
    monkeypatch.setattr(daemon, "ancestry", lambda pid, found: (["python3"], {"pid": pid, "cmd": "python3"}))
    monkeypatch.setattr(daemon, "argv", lambda pid: [b"python3", b"-I", b"-c", b"code", b"n0nce", b"10"])
    monkeypatch.setattr(daemon, "link", lambda path: None)
    monkeypatch.setattr(daemon.os, "kill", lambda pid, sig: sent.append((pid, sig)))
    assert m.who(55)["control"] == "start" and m.who(55)["control"] == "start"
    assert sent == [(55, daemon.signal.SIGUSR1)]


# --------------------------------------------------------------------------
# the sidecar and the setup
# --------------------------------------------------------------------------
def networked(tmp_path, services: dict, volumes: dict | None = None) -> tuple[dict, dict]:
    """(the compose file with_network_monitor writes, the one it was given)."""
    document = {"services": services, **({"volumes": volumes} if volumes else {})}
    compose = tmp_path / "compose.yaml"
    compose.write_text(yaml.safe_dump(document))
    return yaml.safe_load(monitor.with_network_monitor(compose).read_text()), document


def test_the_sidecar_is_one_more_service_and_the_tracer_reaches_it_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(monitor, "ensure_tracer", lambda image: "network-trace-bpftrace:x")
    services = {"k7q2m": {"image": IMAGE, "x-local": True, "volumes": ["team:/team"]}}
    written, given = networked(tmp_path, services, {"team": {}})
    assert written == {**given, "services": {**services, NETWORK_SIDECAR: written["services"][NETWORK_SIDECAR]}}
    assert written["services"][NETWORK_SIDECAR] == {
        "init": True, "command": ["tail", "-f", "/dev/null"], "working_dir": "/", "network_mode": "none",
        "stop_grace_period": "1s", "image": IMAGE, "x-local": True, "pid": "host",
        "cap_add": ["BPF", "PERFMON", "SYS_PTRACE"],
        "volumes": [{"type": "image", "source": "network-trace-bpftrace:x", "target": "/nix"},
                    {"type": "bind", "source": "/sys/kernel/tracing", "target": "/sys/kernel/tracing",
                     "read_only": True}]}


def test_the_sidecar_holds_no_sys_admin_so_inspect_takes_its_read_only_bind():
    """Inspect refuses a host bind into a service with SYS_ADMIN, which could remount it writable."""
    assert "SYS_ADMIN" not in monitor.NETWORK_CAPS and "SYS_ADMIN" in access_monitor.AUDITOR_CAPS


def test_without_a_tracer_the_sidecar_has_no_mounts(tmp_path):
    written, _ = networked(tmp_path, {"default": {"image": IMAGE}})
    assert "volumes" not in written["services"][NETWORK_SIDECAR] and "volumes" not in written


@pytest.mark.parametrize(("info", "allowed"), [
    ({"ServerVersion": "29.5.2", "KernelVersion": "6.8.0-117-generic", "DefaultRuntime": "runc",
      "Runtimes": {"runc": {"status": {"org.opencontainers.runtime-spec.features": '{"mountOptions": ["rro"]}'}}}},
     True),
    ({"ServerVersion": "24.0.7", "KernelVersion": "6.8.0", "DefaultRuntime": "runc",
      "Runtimes": {"runc": {"status": {"org.opencontainers.runtime-spec.features": '{"mountOptions": ["rro"]}'}}}},
     False),
    ({"ServerVersion": "29.0.0", "KernelVersion": "5.10.0", "DefaultRuntime": "runc", "Runtimes": {}}, False),
])
def test_read_only_binds_follow_inspects_rule(info, allowed):
    assert monitor.read_only_binds(info) is allowed


def test_a_wrong_digest_is_refused(tmp_path, monkeypatch):
    fake = tmp_path / "bpftrace"
    fake.write_bytes(b"not the release")
    monkeypatch.setenv(monitor.APPIMAGE_ENV, str(fake))
    with pytest.raises(RuntimeError, match="not the pinned"):
        monitor.appimage("aarch64")
    with pytest.raises(RuntimeError, match="no pinned"):
        monitor.appimage("riscv64")


def test_ensure_tracer_gives_none_and_warns_where_docker_fails(monkeypatch):
    def failing(*args, **kwargs):
        raise FileNotFoundError("docker")
    monkeypatch.setattr(monitor, "docker", failing)
    with pytest.warns(UserWarning, match="no tracer"):
        assert ENSURE_TRACER.__wrapped__(IMAGE) is None


def net(op: str, machine: str, control: str | None = None) -> dict:
    return {"row": "net", "op": op, "machine": machine, **({"control": control} if control else {})}


def test_a_control_must_leave_its_connect_and_its_accept_on_every_machine():
    rows = [net("listen", "a", "start"), net("connect", "a", "start"), net("accept", "a", "start"),
            net("connect", "b", "start"), net("accept", "b", None), net("accept", "b", "end")]
    assert monitor.uncontrolled(rows, ["a", "b"], "start") == ["b:accept"]
    assert monitor.uncontrolled(rows, ["a", "b"], "end") == ["a:accept", "a:connect", "b:connect"]
    assert monitor.uncontrolled(rows, [], "start") == []


def test_the_switch_needs_machines_to_watch_and_not_the_access_log():
    on = build_config("default", "small", [])
    assert monitor.keeps_network_log(SubsetSum(), on)
    assert not monitor.keeps_network_log(SubsetSum(), build_config("default", "small", ["network_log=false"]))
    assert monitor.keeps_network_log(SubsetSum(), build_config("default", "small", ["access_log=false"]))
    assert not monitor.keeps_network_log(Chess(), on)


def test_the_package_ships_both_files_the_sidecar_needs():
    here = Path(monitor.__file__).parent
    assert (here / "network_monitor_daemon.py").read_text() == monitor.DAEMON
    assert monitor.HELPER_PATH.rsplit("/", 1)[0] == monitor.DAEMON_PATH.rsplit("/", 1)[0] == monitor.MONITOR_DIR
