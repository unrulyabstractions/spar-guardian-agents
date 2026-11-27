"""The network log's monitor (src/sandbox/network_log/): the tracer's script and pins, how the daemon reads a kernel
event and the socket tables, the control's rows, the sidecar's mounts and the setup's checks. No docker."""
from __future__ import annotations

import ast
import asyncio
import errno
import json
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from src.configuration.presets import build_config
from src.configuration.settings import ROOT
from src.domains.protein import ProteinDesign
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
    m.netns, m.execs, m.acked, m.known, m.walked = {NS: ["k7q2m"]}, {}, set(), {}, {}
    m.waiting = {}
    m.control_ops = {}
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
    icmp = f"({daemon.ICMP_NETNS} == 4026532448 || {daemon.ICMP_NETNS} == 4026532511)"
    assert "FILTER" not in script and "PARENTS" not in script
    assert daemon.TRACE.count("SKFILTER") == daemon.TRACE.count("ICMPFILTER") == 1
    assert script.count(sk) == script.count(icmp) == 1
    assert script.count(test) == daemon.TRACE.count("FILTER") - 2 == 13
    assert script.count(daemon.PARENTS) == daemon.TRACE.count("PARENTS") == 13
    probes = re.findall(r"^(f(?:exit|entry):vmlinux:\w+|tracepoint:\S+)", script, re.M)
    assert {p.split(":")[-1] for p in probes} >= {"inet_bind", "inet_listen", "inet_stream_connect", "inet_csk_accept",
                                                  "udp_sendmsg", "inet_release", "unix_bind", "unix_listen",
                                                  "unix_stream_connect", "unix_accept", "security_bprm_check",
                                                  "inet_sock_set_state", "ip_icmp_error"}
    assert "fentry:vmlinux:ip_icmp_error, fentry:vmlinux:ipv6_icmp_error" in script
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


def test_an_icmp_error_on_a_connecting_socket_is_a_tcp_error_row():
    """The loopback guard's REJECT reaches the socket while the call holds it: the errno comes from the ICMP probe."""
    m, lines = fake_monitor()
    m.event(event("error", 4_000_000_000, 0, 0, 0, NS, 2, 0, "", 59074, "", 8180, errno.ECONNREFUSED, "", SK, 0, 0),
            clock=1000.0)
    [row] = lines.rows()
    assert {k: row[k] for k in ("row", "machine", "sk", "lport", "rport", "error", "source", "t_kernel")} == \
        {"row": "tcp_error", "machine": "k7q2m", "sk": int(SK), "lport": 59074, "rport": 8180,
         "error": "ECONNREFUSED", "source": "ebpf", "t_kernel": 1004.0}
    assert "tcp_error" in daemon.CAPPED


def test_a_process_gone_first_is_named_through_its_nearest_ancestor_still_there(monkeypatch):
    """bash's /dev/tcp child exits before /proc is read: its row takes its parent's chain and root, with via, so its
    origin and call follow; with every ancestor gone it reads gone."""
    m, _ = fake_monitor()
    del m.who
    alive = {60: {"uid": 1001, "ppid": 50, "ns_pid": 7}, 50: {"uid": 1001, "ppid": 1, "ns_pid": 2}}
    monkeypatch.setattr(daemon, "status", lambda pid: alive.get(int(pid)))
    monkeypatch.setattr(daemon, "ancestry", lambda pid, found: ([f"argv of {pid}"], {"pid": 50, "cmd": "bash -c x"}))
    monkeypatch.setattr(daemon, "argv", lambda pid: [b"bash"] if int(pid) in alive else [])
    m.execs[77] = "/usr/bin/bash"
    named = m.who(77, [60, 50])
    assert named == {"exe": "/usr/bin/bash", "exe_from": "exec", "cwd": None, "cmd": None,
                     "chain": ["argv of 60"], "root": {"pid": 50, "cmd": "bash -c x"}, "via": 60}
    assert m.who(77, [99, 50])["via"] == 50
    assert m.who(77, [99, 98]) == {"gone": True, "exe": "/usr/bin/bash", "exe_from": "exec"}
    assert m.who(77, [1, 0]) == {"gone": True, "exe": "/usr/bin/bash", "exe_from": "exec"}


def test_a_call_whose_shell_and_timeout_left_too_is_named_by_their_earlier_walk(monkeypatch):
    """A tool call's shell and timeout leave as soon as its command ends: the child's row reads their walk from an
    earlier row or exec (via_from earlier). A zombie keeps its status but no argv: its walk names nothing, so the
    earlier one stands."""
    m, _ = fake_monitor()
    del m.who
    alive = {60: {"uid": 0, "ppid": 50, "ns_pid": 7}, 50: {"uid": 0, "ppid": 1, "ns_pid": 2}}
    args = {60: [b"bash", b"--login", b"-c", b"x"], 50: [b"timeout", b"-k", b"5s", b"300s", b"bash"]}
    root = {"pid": 50, "cmd": "timeout -k 5s 300s bash --login -c x"}
    monkeypatch.setattr(daemon, "status", lambda pid: alive.get(int(pid)))
    monkeypatch.setattr(daemon, "ancestry", lambda pid, found: ([f"argv of {pid}"], root if args.get(50) else
                                                                {**root, "cmd": ""}))
    monkeypatch.setattr(daemon, "argv", lambda pid: args.get(int(pid), []))
    monkeypatch.setattr(daemon, "link", lambda path: None)
    m.event(event("exec", 1, 60, 60, 0, NS, 0, 0, "/usr/bin/bash", 0, "", 0, 0, "bash", "0", 50, 1), clock=0.0)
    assert m.walked[60] == (["argv of 60"], root)
    alive.clear()
    assert m.who(77, [60, 50]) == {"exe": None, "cwd": None, "cmd": None, "chain": ["argv of 60"], "root": root,
                                   "via": 60, "via_from": "earlier"}
    alive.update({60: {"uid": 0, "ppid": 50, "ns_pid": 7}})
    args.clear()                                # a zombie: status, no argv, its root's argv gone too
    assert m.walk(60) is None and m.walked[60] == (["argv of 60"], root)
    assert m.who(77, [60, 50])["via_from"] == "earlier"


def test_the_walks_kept_are_bounded(monkeypatch):
    m, _ = fake_monitor()
    monkeypatch.setattr(daemon, "WALKED_MAX", 2)
    monkeypatch.setattr(daemon, "status", lambda pid: {"uid": 0, "ppid": 1, "ns_pid": 2})
    monkeypatch.setattr(daemon, "ancestry", lambda pid, found: (["x"], {"pid": pid, "cmd": "x"}))
    monkeypatch.setattr(daemon, "argv", lambda pid: [b"x"])
    for pid in (5, 6, 7):
        m.walk(pid)
    assert list(m.walked) == [6, 7]


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


def test_a_new_socket_with_no_owner_waits_for_the_read_that_names_it(monkeypatch):
    """A read lists the processes, then the tables: a control opened in between has no owner in that read. Its rows
    wait for the next read, which names the control, so the control gets its signal."""
    control = {"uid": 0, "exe": "/usr/bin/python3", "cmd": "python3 -I -c x n0nce 30", "chain": [], "root": {},
               "control": "start"}
    m, lines = fake_monitor(who=control)
    sent = []
    monkeypatch.setattr(daemon.os, "kill", lambda pid, sig: sent.append((pid, sig)))

    def sockets(pid):
        return {(NS, 1): {"fields": {"op": "listen", "netns": NS, "lport": 50341}, "pid": pid},
                (NS, 2): {"fields": {"op": "connect", "netns": NS, "lport": 55634}, "pid": pid},
                (NS, 3): {"fields": {"op": "accept", "netns": NS, "lport": 50341}, "pid": pid}}
    snapshots = iter([{}, sockets(None), sockets(55), sockets(55)])
    m.snapshot = lambda: next(snapshots)
    m.table_rows(at_start=True)
    m.table_rows(at_start=False)
    assert lines.rows() == [] and sent == []                 # no owner yet: nothing written, nothing answered
    m.table_rows(at_start=False)
    assert [(r["op"], r["pid"], r["control"]) for r in lines.rows()] == \
        [("listen", 55, "start"), ("connect", 55, "start"), ("accept", 55, "start")]
    assert sent == [(55, daemon.signal.SIGUSR1)]
    m.table_rows(at_start=False)
    assert len(lines.rows()) == 3                            # written once


def test_a_socket_whose_owner_never_shows_is_written_without_one(monkeypatch):
    """After OWNER_WAIT_S a socket still without an owner gets its row as before; one that went while it waited gets
    its row and its close at once; and the first read of a run waits for nothing."""
    m, lines = fake_monitor()
    clock = [100.0]
    monkeypatch.setattr(daemon.time, "monotonic", lambda: clock[0])
    stays = {(NS, 1): {"fields": {"op": "listen", "netns": NS, "lport": 8123}, "pid": None}}
    brief = {(NS, 2): {"fields": {"op": "connect", "netns": NS, "lport": 46152}, "pid": None}}
    snapshots = iter([{**stays, **brief}, dict(stays), dict(stays), dict(stays)])
    m.snapshot = lambda: next(snapshots)
    m.table_rows(at_start=False)
    assert lines.rows() == []
    clock[0] += 0.3
    m.table_rows(at_start=False)                             # the connect went while it waited: its row and its close
    assert [(r["op"], r["pid"]) for r in lines.rows()] == [("connect", None), ("close", None)]
    clock[0] += daemon.OWNER_WAIT_S
    m.table_rows(at_start=False)                             # the listen waited long enough
    assert [(r["op"], r["pid"]) for r in lines.rows()][2:] == [("listen", None)]
    m.table_rows(at_start=False)
    assert len(lines.rows()) == 3
    first, lines2 = fake_monitor()
    first.snapshot = lambda: dict(stays)
    first.table_rows(at_start=True)
    assert [(r["op"], r["at_start"]) for r in lines2.rows()] == [("listen", True)]


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


def test_a_controls_row_names_it_and_sends_no_signal_itself(monkeypatch):
    m, _ = fake_monitor()
    del m.who
    sent = []
    monkeypatch.setattr(daemon, "status", lambda pid: {"uid": 0, "ppid": 1, "ns_pid": 9})
    monkeypatch.setattr(daemon, "ancestry", lambda pid, found: (["python3"], {"pid": pid, "cmd": "python3"}))
    monkeypatch.setattr(daemon, "argv", lambda pid: [b"python3", b"-I", b"-c", b"code", b"n0nce", b"10"])
    monkeypatch.setattr(daemon, "link", lambda path: None)
    monkeypatch.setattr(daemon.os, "kill", lambda pid, sig: sent.append((pid, sig)))
    assert m.who(55)["control"] == "start" and m.who(55)["control"] == "start"
    assert sent == []


def test_a_control_gets_its_signal_once_its_connect_and_its_accept_are_written(monkeypatch):
    """On 2026-11-22 the signal came on the control's bind row: the control exited while the daemon, 2 to 5 ms behind
    at 15 live runs, had yet to read its connect and accept, which then named a gone process without the nonce. 15 runs
    of ian_seed_prototyping_0 read broken from it, 8 of them at setup."""
    control = {"uid": 0, "exe": "/usr/bin/python3", "cmd": "python3 -I -c x n0nce 30", "chain": [], "root": {},
               "control": "start"}
    m, lines = fake_monitor(who=control)
    sent = []
    monkeypatch.setattr(daemon.os, "kill", lambda pid, sig: sent.append((pid, sig)))
    for op in ("bind", "listen", "connect"):
        m.write_net({"op": op, "netns": NS}, 55)
    assert sent == []
    m.write_net({"op": "accept", "netns": NS}, 55)
    assert sent == [(55, daemon.signal.SIGUSR1)]
    m.write_net({"op": "close", "netns": NS}, 55)
    m.write_net({"op": "accept", "netns": NS}, 55)
    assert sent == [(55, daemon.signal.SIGUSR1)]
    assert [row["op"] for row in lines.rows()] == ["bind", "listen", "connect", "accept", "close", "accept"]
    m.write_net({"op": "accept", "netns": NS}, 56)       # another control: its accept alone is not enough
    assert sent == [(55, daemon.signal.SIGUSR1)]


def test_an_ordinary_processs_rows_send_nothing(monkeypatch):
    m, _ = fake_monitor()
    sent = []
    monkeypatch.setattr(daemon.os, "kill", lambda pid, sig: sent.append((pid, sig)))
    for op in ("connect", "accept"):
        m.write_net({"op": op, "netns": NS}, 77)
    assert sent == [] and m.control_ops == {}


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


def test_a_traced_compose_and_an_untraced_one_never_share_a_file(tmp_path, monkeypatch):
    """The build folder is shared: a process with no tracer (the unit tests' stand-in) writing at once must not
    overwrite the compose a traced run starts from."""
    compose = tmp_path / "compose-x.yaml"
    compose.write_text(yaml.safe_dump({"services": {"default": {"image": IMAGE}}}))
    plain = monitor.with_network_monitor(compose)
    monkeypatch.setattr(monitor, "ensure_tracer", lambda image: "network-trace-bpftrace:x")
    traced = monitor.with_network_monitor(compose)
    assert (plain.name, traced.name) == ("compose-x-networked.yaml", "compose-x-networked-traced.yaml")
    assert "/nix" in traced.read_text() and "/nix" not in plain.read_text()


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
    class Hosted(ProteinDesign):
        watched = None                      # a domain whose honeypot lives on the host has no machine to watch

    on = build_config("default", "small", [])
    assert monitor.keeps_network_log(ProteinDesign(), on)
    assert not monitor.keeps_network_log(ProteinDesign(), build_config("default", "small", ["network_log=false"]))
    assert monitor.keeps_network_log(ProteinDesign(), build_config("default", "small", ["access_log=false"]))
    assert not monitor.keeps_network_log(Hosted(), on)


def test_the_package_ships_both_files_the_sidecar_needs():
    here = Path(monitor.__file__).parent
    assert (here / "network_monitor_daemon.py").read_text() == monitor.DAEMON
    assert monitor.HELPER_PATH.rsplit("/", 1)[0] == monitor.DAEMON_PATH.rsplit("/", 1)[0] == monitor.MONITOR_DIR


# --------------------------------------------------------------------------
# the smoke suites' reading of a log
# --------------------------------------------------------------------------
@pytest.mark.parametrize(("block", "worked"), [
    (None, True), ({"status": "off"}, True), ({"status": "valid", "partial_reasons": []}, True),
    ({"status": "partial", "partial_reasons": ["procnet"]}, True),
    ({"status": "partial", "partial_reasons": ["procnet", "gap"]}, False), ({"status": "broken"}, False),
])
def test_a_smoke_reads_a_procnet_log_as_all_the_host_gives(block, worked):
    from tests.smoke.common import network_worked
    assert network_worked(block) is worked


# --------------------------------------------------------------------------
# the controls' tries and their reasons
# --------------------------------------------------------------------------
class Sidecar:
    """The network sidecar: HEALTH answers `alive` while the daemon runs, read_file gives the log's rows."""

    def __init__(self, rows: list[dict], alive: bool = True, health: str = "free: 52 KiB on /") -> None:
        self.rows, self.alive, self.health, self.commands = rows, alive, health, []

    async def exec(self, command, **_):
        self.commands.append(command)
        if command[2] == monitor.HEALTH:
            out = ("alive" if self.alive else "dead") + "\n" + self.health + "\n"
            return SimpleNamespace(success=True, returncode=0, stdout=out, stderr="")
        if command[2] == monitor.STOP:
            if self.alive:              # a live daemon writes its stopped row; a dead one writes nothing
                self.rows.append({"row": "stopped", "seq": 99})
            self.alive = False
        return SimpleNamespace(success=True, returncode=0, stdout="", stderr="")

    async def read_file(self, path):
        assert path == monitor.LOG_PATH
        return "".join(json.dumps(row) + "\n" for row in self.rows)


def controls_on(monkeypatch, sidecar: Sidecar, written: dict[str, list[bool]]) -> list[tuple[str, str]]:
    """monitor.control replaced: each call on a machine pops whether this try's rows reach the log (and the daemon
    answers). Returns the calls made."""
    calls = []

    async def control(machine, nonce):
        name = "start" if nonce == "s" else "end"
        calls.append((machine, name))
        wrote = written[machine].pop(0)
        if wrote:
            sidecar.rows += [net("connect", machine, name), net("accept", machine, name)]
        else:                       # the race of 2026-11-22: the rows are there, named through a parent
            sidecar.rows += [net("connect", machine), net("accept", machine)]
        return {"port": 1, "acked": wrote}

    monkeypatch.setattr(monitor, "control", control)
    monkeypatch.setattr(monitor, "sandbox", lambda name: sidecar)
    return calls


RECORD = {"control_machines": ["a", "b"], "nonces": {"start": "s", "end": "e"}, "pid": 4242}


def test_a_control_whose_rows_are_missing_is_tried_again_on_that_machine_alone(monkeypatch):
    sidecar = Sidecar([{"row": "ready", "seq": 1}])
    calls = controls_on(monkeypatch, sidecar, {"a": [True], "b": [False, True]})
    missing, why = asyncio.run(monitor.run_controls(sidecar, RECORD, "start"))
    assert missing == [] and calls == [("a", "start"), ("b", "start"), ("b", "start")]
    assert why == [f"try 1 on b: no connect and accept row of the control in {monitor.CONTROL_HOLD_S} s"]


def test_a_control_that_never_leaves_its_rows_stays_missing_after_every_try(monkeypatch):
    sidecar = Sidecar([{"row": "ready", "seq": 1}])
    controls_on(monkeypatch, sidecar, {"a": [True], "b": [False] * monitor.CONTROL_TRIES})
    missing, why = asyncio.run(monitor.run_controls(sidecar, RECORD, "start"))
    assert missing == ["b:accept", "b:connect"] and len(why) == monitor.CONTROL_TRIES


def test_a_dead_monitor_is_tried_no_more_and_says_why(monkeypatch):
    sidecar = Sidecar([{"row": "ready", "seq": 1}], alive=False,
                      health="stderr: OSError: [Errno 28] No space left on device\nfree: 0 KiB on /")
    calls = controls_on(monkeypatch, sidecar, {"a": [], "b": []})
    missing, why = asyncio.run(monitor.run_controls(sidecar, RECORD, "end"))
    assert calls == [] and missing == ["a:accept", "a:connect", "b:accept", "b:connect"]
    assert why == ["try 1: the monitor (pid 4242) is not running: stderr: OSError: [Errno 28] No space left on "
                   "device free: 0 KiB on /"]


def test_a_control_that_raises_is_a_reason_and_the_other_machines_still_run(monkeypatch):
    sidecar = Sidecar([{"row": "ready", "seq": 1}])
    controls_on(monkeypatch, sidecar, {"a": [True], "b": [True]})
    real = monitor.control

    async def flaky(machine, nonce, tries=[0]):
        if machine == "a" and not tries[0]:
            tries[0] += 1
            raise RuntimeError("exec failed")
        return await real(machine, nonce)

    monkeypatch.setattr(monitor, "control", flaky)
    missing, why = asyncio.run(monitor.run_controls(sidecar, RECORD, "start"))
    assert missing == [] and why == ["try 1 on a: exec failed"]


def test_the_capture_says_why_a_control_is_missing_and_why_no_stopped_row_came(monkeypatch):
    """The four runs of 2026-11-22 whose daemon died with the disk full at 05:45 read broken with error null."""
    sidecar = Sidecar([{"row": "ready", "seq": 1}], alive=False, health="free: 0 KiB on /")
    controls_on(monkeypatch, sidecar, {"a": [], "b": []})
    state = SimpleNamespace(store={monitor.STORE: dict(RECORD)})
    state.store = Store(state.store)
    asyncio.run(monitor.capture_network(state))
    record = state.store.get(monitor.STORE)
    assert "end control left no row for ['a:accept', 'a:connect', 'b:accept', 'b:connect']" in record["error"]
    assert "not running: free: 0 KiB on /" in record["error"] and "no stopped row: free: 0 KiB on /" in record["error"]


def test_a_capture_that_needed_a_second_try_keeps_its_reason_and_no_error(monkeypatch):
    sidecar = Sidecar([{"row": "ready", "seq": 1}])
    controls_on(monkeypatch, sidecar, {"a": [False, True], "b": [True]})
    state = SimpleNamespace(store=Store({monitor.STORE: {**RECORD, "retried": {"start": ["x"]}}}))
    asyncio.run(monitor.capture_network(state))
    record = state.store.get(monitor.STORE)
    assert record["error"] is None and record["rows"][-1]["row"] == "stopped"
    assert record["retried"] == {"start": ["x"], "end": [
        f"try 1 on a: no connect and accept row of the control in {monitor.CONTROL_HOLD_S} s"]}
    assert monitor.uncontrolled(record["rows"], ["a", "b"], "end") == []


def test_the_start_raises_with_every_reason(monkeypatch):
    sidecar = Sidecar([{"row": "ready", "seq": 1}])
    controls_on(monkeypatch, sidecar, {"a": [True], "b": [False] * monitor.CONTROL_TRIES})

    async def no_containers():
        return {"a": {"container": "ca", "pid": 1}, "b": {"container": "cb", "pid": 2}}

    sidecar.exec_real = sidecar.exec

    async def exec_(command, **kwargs):
        if command[:2] == ["bash", "-c"] and command[2] == monitor.START:
            return SimpleNamespace(success=True, returncode=0, stdout=json.dumps(
                {"row": "ready", "pid": 4242, "method": "ebpf"}) + "\n", stderr="")
        if command[0] == "sh":
            return SimpleNamespace(success=True, returncode=0, stdout="", stderr="")
        return await sidecar.exec_real(command, **kwargs)

    async def write_file(path, text):
        return None

    sidecar.exec, sidecar.write_file = exec_, write_file
    monkeypatch.setattr(monitor, "sample_containers", no_containers)
    monkeypatch.setattr(monitor, "seat_record", lambda benchmark, state: [
        {"machine": "a", "uid": 1000}, {"machine": "b", "uid": 1001}])
    monkeypatch.setattr(monitor.secrets, "token_hex", lambda n: "s")
    state = SimpleNamespace(store=Store({}))
    with pytest.raises(RuntimeError, match=r"left no row for \['b:accept', 'b:connect'\]: try 1 on b: .*try 3 on b"):
        asyncio.run(monitor.start_network_monitor(None)(state, None))


class Store:
    def __init__(self, data: dict) -> None:
        self.data = data

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value) -> None:
        self.data[key] = value


def test_a_docker_failure_names_dockers_stderr(monkeypatch):
    from src.sandbox import docker_images

    def refused(command, **_):
        return subprocess.CompletedProcess(command, 125, b"", b"docker: Error response from daemon: failed to create "
                                           b"prepare snapshot dir: no space left on device.")
    monkeypatch.setattr(docker_images.subprocess, "run", refused)
    with pytest.raises(subprocess.CalledProcessError, match="exit status 125. docker said: .*no space left on device"):
        docker_images.docker("run", "--rm", "x")
    assert docker_images.docker("run", "x", check=False).returncode == 125
