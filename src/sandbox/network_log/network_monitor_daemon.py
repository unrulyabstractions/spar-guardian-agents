"""Run in the network log's sidecar as root: record every socket call of the agents' machines. It records only: it
blocks, drops and answers nothing. Standard library only, and access_monitor_daemon.py beside it for what /proc says of
a process.

The sidecar shares the host's pid namespace and holds BPF, PERFMON and SYS_PTRACE; for the tracer it also gets the
tracer's image at /nix and /sys/kernel/tracing, both read only (network_monitor.py). The plan comes on stdin:

    {"log": path, "max_rows": n, "poll_s": s, "method": "auto" | "procnet",
     "nonces": {control: nonce},
     "machines": {service: {"container": id, "pid": init's host pid}}}

Two methods, named in the ready row:
  ebpf     bpftrace (the static build in the volume, a JSON-output subprocess) with fexit probes on the kernel's socket
           calls: inet_bind, inet6_bind, inet_listen, inet_stream_connect, inet_dgram_connect, inet_csk_accept,
           udp_sendmsg with an address (the first send per process and destination), inet_release, unix_bind,
           unix_listen, unix_stream_connect, unix_dgram_connect, unix_accept, fentry on security_bprm_check for
           the exe of a process gone before /proc is read, the tracepoint sock:inet_sock_set_state for a TCP
           connect leaving SYN_SENT and fentry on ip_icmp_error and ipv6_icmp_error for an ICMP error reaching one
           (how a connect that did not wait ended). The script is filtered to the machines'
           network namespaces: the trace sees every container on the host. The kernel does not wait for the row, so a
           process may be gone before /proc is read; its row keeps the kernel's pid, uid and comm and is named through
           its parent or grandparent when one is still there (via), else reads gone.
  procnet  the fallback when no tracer is mounted or it cannot attach (a trace_failed row says why): each network
           namespace's socket tables (/proc/<init>/net/{tcp,tcp6,udp,udp6,unix}) and every process's socket
           descriptors, read every poll_s, and a row for each socket that appears, changes kind or goes. A connection
           that opens and closes between two reads leaves no row, and a unix client is not seen. A TCP connect's row
           holds its state at the read (tcp_state), and a read that finds it out of SYN_SENT writes a tcp_state row.
Both read the tables once at the start: what already listens or is connected then gets a row with at_start.

The rows, each a JSON object on one line with seq, t and row:
  ready        pid, method, netns ({inode: [machines]}) and probes for ebpf
  trace_failed the tracer could not start: error; procnet follows
  net          one socket call: op (bind, listen, connect, accept, send, close), proto (tcp, udp, unix, raw), family
               (inet, inet6, unix), type (stream, dgram, seqpacket, raw), netns, machine, pid, tid, uid, comm, laddr,
               lport, raddr, rport (a unix socket's path in laddr, or raddr for a connect, with "@" before an abstract
               name), result ("ok" or the errno's name), source (ebpf or procnet), at_start, and who as the access
               log names an opener (uid, exe, cwd, cmd, chain, root, and control for the harness's control with a
               nonce in its argv), or the chain and root of an ancestor with via its pid (and via_from earlier when
               that walk came before the row: the process's own at its exec, or a gone ancestor's), or gone, with the
               exec's exe when it was seen; sk the socket's kernel address for ebpf
  tcp_state    a TCP connect left SYN_SENT: netns, machine, sk (ebpf) or socket (the inode, procnet), lport, rport,
               old, new (ESTABLISHED, or CLOSE for a connect that failed) and error (the errno's name the kernel set,
               None for none or unknown)
  tcp_error    an ICMP error reached a TCP connect in SYN_SENT: netns, machine, sk, lport, rport and error (ebpf)
  lost         the tracer lost n events
  restart, machine_down  the poll found a machine under a new init, which the trace's filter no longer covers, or
               nowhere
  row_failed   an event that could not be described
  stopped      after SIGTERM: rows seen, dropped, lost

The control. The harness's control (network_monitor.CONTROL) listens on loopback, connects to itself and accepts, with
the nonce in its argv, and waits for SIGUSR1. The daemon sends it once it has written a row of each of CONTROL_OPS of
that process naming the nonce (Monitor.answer), so the control lives until those rows are written. A signal on its first
row (the bind) let it exit while the daemon, a few ms behind under load, had yet to read its connect and accept: those
rows then named a gone process through its parent, without the nonce, and the control read as missing.

Agent-made text (comm, paths, argv) reaches the log only through json.dumps.
"""
from __future__ import annotations

import errno
import glob
import ipaddress
import json
import os
import queue
import signal
import subprocess
import sys
import threading
import time

if __name__ == "__main__":     # in the sidecar, run by `python3 -I`, which keeps this file's folder off sys.path
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from access_monitor_daemon import CMD_CHARS, WALKS, ancestry, argv, find_init, link, read_bytes, shell_words, status
else:                          # imported from the checkout (the unit tests)
    from src.sandbox.access_log.access_monitor_daemon import (
        CMD_CHARS,
        WALKS,
        ancestry,
        argv,
        find_init,
        link,
        read_bytes,
        shell_words,
        status,
    )

TRACER_GLOB = "/nix/store/*-bpftrace/bin/bpftrace"
WALKED_MAX = 50_000                   # the walks Monitor.walked keeps, oldest dropped first
ATTACH_S = 60
STOP_S = 15
CONTROL_OPS = ("connect", "accept")   # the control's rows the daemon writes before it answers, and the log must hold
FAMILIES = {1: "unix", 2: "inet", 10: "inet6"}
TYPES = {1: "stream", 2: "dgram", 3: "raw", 5: "seqpacket"}
# The kernel's events: name -> (op, which side holds the unix path)
EVENTS = {"bind": "bind", "listen": "listen", "connect": "connect", "accept": "accept", "send": "send",
          "close": "close", "unix_bind": "bind", "unix_listen": "listen", "unix_connect": "connect",
          "unix_accept": "accept"}
# /proc/net/tcp states
TCP_LISTEN, TCP_CLOSE, TCP_ESTABLISHED, TCP_SYN_SENT = "0A", "07", "01", "02"
TCP_STATES = {1: "ESTABLISHED", 2: "SYN_SENT", 3: "SYN_RECV", 4: "FIN_WAIT1", 5: "FIN_WAIT2", 6: "TIME_WAIT",
              7: "CLOSE", 8: "CLOSE_WAIT", 9: "LAST_ACK", 10: "LISTEN", 11: "CLOSING"}
UNIX_LISTENING = 0x10000              # __SO_ACCEPTCON in /proc/net/unix's Flags
UNIX_CONNECTED = "03"                 # SS_CONNECTED

# One print per event: (event, nsecs, pid, tid, uid, netns, type, family, a, port a, b, port b, ret, comm, sk, ppid,
# gppid). a is the local end and b the remote one; a unix socket's path is a (b for a connect) and its port slot is 1
# for an abstract name. sk is the socket's kernel address, ppid and gppid the caller's parent and grandparent as the
# kernel holds them (PARENTS), so a process gone before /proc is read is named through an ancestor still there. FILTER
# becomes the namespaces' test on the caller, SKFILTER on the socket. An abstract name's bytes come bounded by the
# address's length; struct unix_address holds refcnt and len before name.
#
# A state print is a TCP socket leaving SYN_SENT: (state, nsecs, 0, 0, 0, netns, old, new, "", sport, "", dport,
# sk_err, "", sk, 0, 0). It fires in whatever context the kernel changes the state (a softirq for a handshake), so its
# netns is the socket's own. The kernel sets sk_err before it closes a refused or timed-out connect.
#
# An error print is an ICMP error reaching a TCP socket in SYN_SENT: (error, nsecs, 0, 0, 0, netns, 2, 0, "", sport,
# "", dport, errno, "", sk, 0, 0). When it lands while the connecting call still holds the socket (a loopback REJECT,
# as the loopback guard's), the kernel queues the error and leaves the socket in SYN_SENT; the program reads the error
# and closes the socket, whose state change then carries none. ICMPFILTER is the namespaces' test on that socket.
TRACE = r"""
fexit:vmlinux:inet_bind, fexit:vmlinux:inet6_bind /FILTER/ {
  $a = (struct sockaddr_in *)args.uaddr; $ip = ntop(2, $a->sin_addr.s_addr);
  if ($a->sin_family == 10) { $ip = ntop(10, ((struct sockaddr_in6 *)args.uaddr)->sin6_addr.in6_u.u6_addr8); }
  print(("bind", nsecs, pid, tid, uid, curtask->nsproxy->net_ns->ns.inum, args.sock->type, $a->sin_family, $ip,
         args.sock->sk->__sk_common.skc_num, "", 0, retval, comm, (uint64)args.sock->sk, PARENTS));
}
fexit:vmlinux:inet_listen /FILTER/ {
  $sk = args.sock->sk; $ip = ntop(2, $sk->__sk_common.skc_rcv_saddr);
  if ($sk->__sk_common.skc_family == 10) { $ip = ntop(10, $sk->__sk_common.skc_v6_rcv_saddr.in6_u.u6_addr8); }
  print(("listen", nsecs, pid, tid, uid, curtask->nsproxy->net_ns->ns.inum, args.sock->type,
         $sk->__sk_common.skc_family, $ip, $sk->__sk_common.skc_num, "", 0, retval, comm, (uint64)$sk, PARENTS));
}
fexit:vmlinux:inet_stream_connect, fexit:vmlinux:inet_dgram_connect /FILTER/ {
  $a = (struct sockaddr_in *)args.uaddr; $sk = args.sock->sk;
  $rip = ntop(2, $a->sin_addr.s_addr); $lip = ntop(2, $sk->__sk_common.skc_rcv_saddr);
  if ($a->sin_family == 10) {
    $rip = ntop(10, ((struct sockaddr_in6 *)args.uaddr)->sin6_addr.in6_u.u6_addr8);
    $lip = ntop(10, $sk->__sk_common.skc_v6_rcv_saddr.in6_u.u6_addr8);
  }
  print(("connect", nsecs, pid, tid, uid, curtask->nsproxy->net_ns->ns.inum, args.sock->type, $a->sin_family, $lip,
         $sk->__sk_common.skc_num, $rip, bswap($a->sin_port), retval, comm, (uint64)$sk, PARENTS));
}
fexit:vmlinux:inet_csk_accept /retval != 0 && FILTER/ {
  $sk = retval; $lip = ntop(2, $sk->__sk_common.skc_rcv_saddr); $rip = ntop(2, $sk->__sk_common.skc_daddr);
  if ($sk->__sk_common.skc_family == 10) {
    $lip = ntop(10, $sk->__sk_common.skc_v6_rcv_saddr.in6_u.u6_addr8);
    $rip = ntop(10, $sk->__sk_common.skc_v6_daddr.in6_u.u6_addr8);
  }
  print(("accept", nsecs, pid, tid, uid, curtask->nsproxy->net_ns->ns.inum, 1, $sk->__sk_common.skc_family, $lip,
         $sk->__sk_common.skc_num, $rip, bswap($sk->__sk_common.skc_dport), 0, comm, (uint64)$sk, PARENTS));
}
fexit:vmlinux:udp_sendmsg /args.msg->msg_name != 0 && FILTER/ {
  $a = (struct sockaddr_in *)args.msg->msg_name;
  if (@sent[pid, $a->sin_addr.s_addr, $a->sin_port] == 0) {
    @sent[pid, $a->sin_addr.s_addr, $a->sin_port] = 1;
    print(("send", nsecs, pid, tid, uid, curtask->nsproxy->net_ns->ns.inum, 2, 2,
           ntop(2, args.sk->__sk_common.skc_rcv_saddr), args.sk->__sk_common.skc_num, ntop(2, $a->sin_addr.s_addr),
           bswap($a->sin_port), retval, comm, (uint64)args.sk, PARENTS));
  }
}
fexit:vmlinux:udpv6_sendmsg /args.msg->msg_name != 0 && FILTER/ {
  $a = (struct sockaddr_in6 *)args.msg->msg_name;
  if (@sent6[pid, $a->sin6_port] == 0) {
    @sent6[pid, $a->sin6_port] = 1;
    print(("send", nsecs, pid, tid, uid, curtask->nsproxy->net_ns->ns.inum, 2, 10,
           ntop(10, args.sk->__sk_common.skc_v6_rcv_saddr.in6_u.u6_addr8), args.sk->__sk_common.skc_num,
           ntop(10, $a->sin6_addr.in6_u.u6_addr8), bswap($a->sin6_port), retval, comm, (uint64)args.sk, PARENTS));
  }
}
fentry:vmlinux:inet_release /args.sock->sk != 0 && args.sock->sk->__sk_common.skc_num != 0 && FILTER/ {
  $sk = args.sock->sk; $lip = ntop(2, $sk->__sk_common.skc_rcv_saddr); $rip = ntop(2, $sk->__sk_common.skc_daddr);
  if ($sk->__sk_common.skc_family == 10) {
    $lip = ntop(10, $sk->__sk_common.skc_v6_rcv_saddr.in6_u.u6_addr8);
    $rip = ntop(10, $sk->__sk_common.skc_v6_daddr.in6_u.u6_addr8);
  }
  print(("close", nsecs, pid, tid, uid, curtask->nsproxy->net_ns->ns.inum, args.sock->type,
         $sk->__sk_common.skc_family, $lip, $sk->__sk_common.skc_num, $rip, bswap($sk->__sk_common.skc_dport), 0,
         comm, (uint64)$sk, PARENTS));
}
fexit:vmlinux:unix_bind /FILTER/ {
  $u = (uint8 *)args.uaddr; $n = args.addr_len - 2; $abs = 0;
  if ($n > 0 && *($u + 2) == 0) { $abs = 1; }
  print(("unix_bind", nsecs, pid, tid, uid, curtask->nsproxy->net_ns->ns.inum, args.sock->type, 1,
         str($u + 2 + $abs, $n - $abs + 1), $abs, "", 0, retval, comm, (uint64)args.sock->sk,
         PARENTS));
}
fexit:vmlinux:unix_listen /FILTER/ {
  $ad = ((struct unix_sock *)args.sock->sk)->addr; $u = (uint8 *)$ad + 8; $n = 0; $abs = 0;
  if ($ad != 0) { $n = $ad->len - 2; }
  if ($n > 0 && *($u + 2) == 0) { $abs = 1; }
  print(("unix_listen", nsecs, pid, tid, uid, curtask->nsproxy->net_ns->ns.inum, args.sock->type, 1,
         str($u + 2 + $abs, $n - $abs + 1), $abs, "", 0, retval, comm, (uint64)args.sock->sk,
         PARENTS));
}
fexit:vmlinux:unix_stream_connect /FILTER/ {
  $u = (uint8 *)args.uaddr; $n = args.addr_len - 2; $abs = 0;
  if ($n > 0 && *($u + 2) == 0) { $abs = 1; }
  print(("unix_connect", nsecs, pid, tid, uid, curtask->nsproxy->net_ns->ns.inum, args.sock->type, 1, "", 0,
         str($u + 2 + $abs, $n - $abs + 1), $abs, retval, comm, (uint64)args.sock->sk,
         PARENTS));
}
fexit:vmlinux:unix_dgram_connect /FILTER/ {
  $u = (uint8 *)args.addr; $n = args.alen - 2; $abs = 0;
  if ($n > 0 && *($u + 2) == 0) { $abs = 1; }
  print(("unix_connect", nsecs, pid, tid, uid, curtask->nsproxy->net_ns->ns.inum, args.sock->type, 1, "", 0,
         str($u + 2 + $abs, $n - $abs + 1), $abs, retval, comm, (uint64)args.sock->sk,
         PARENTS));
}
fexit:vmlinux:unix_accept /retval == 0 && FILTER/ {
  $ad = ((struct unix_sock *)args.sock->sk)->addr; $u = (uint8 *)$ad + 8; $n = 0; $abs = 0;
  if ($ad != 0) { $n = $ad->len - 2; }
  if ($n > 0 && *($u + 2) == 0) { $abs = 1; }
  print(("unix_accept", nsecs, pid, tid, uid, curtask->nsproxy->net_ns->ns.inum, args.sock->type, 1,
         str($u + 2 + $abs, $n - $abs + 1), $abs, "", 0, retval, comm, (uint64)args.sock->sk,
         PARENTS));
}
fentry:vmlinux:security_bprm_check /FILTER/ {
  print(("exec", nsecs, pid, tid, uid, curtask->nsproxy->net_ns->ns.inum, 0, 0, str(args.bprm->filename), 0, "", 0,
         0, comm, (uint64)0, PARENTS));
}
fentry:vmlinux:ip_icmp_error, fentry:vmlinux:ipv6_icmp_error
    /args.sk->sk_protocol == 6 && args.sk->__sk_common.skc_state == 2 && ICMPFILTER/ {
  print(("error", nsecs, 0, 0, 0, args.sk->__sk_common.skc_net.net->ns.inum, 2, 0, "", args.sk->__sk_common.skc_num,
         "", bswap(args.sk->__sk_common.skc_dport), args.err, "", (uint64)args.sk, 0, 0));
}
tracepoint:sock:inet_sock_set_state /args.protocol == 6 && args.oldstate == 2 && SKFILTER/ {
  $sk = (struct sock *)args.skaddr;
  print(("state", nsecs, 0, 0, 0, $sk->__sk_common.skc_net.net->ns.inum, args.oldstate, args.newstate, "", args.sport,
         "", args.dport, $sk->sk_err, "", (uint64)args.skaddr, 0, 0));
}
END { clear(@sent); clear(@sent6); }
"""


PARENTS = "curtask->real_parent->tgid, curtask->real_parent->real_parent->tgid"
SOCKET_NETNS = "((struct sock *)args.skaddr)->__sk_common.skc_net.net->ns.inum"
ICMP_NETNS = "args.sk->__sk_common.skc_net.net->ns.inum"


def trace_script(namespaces: list[int]) -> str:
    """TRACE filtered to these network namespaces' inodes, on the caller (FILTER) and on the socket (SKFILTER for the
    state tracepoint, ICMPFILTER for the ICMP errors)."""
    def test(of: str) -> str:
        return "(" + " || ".join(f"{of} == {inode}" for inode in sorted(namespaces)) + ")"
    return (TRACE.replace("SKFILTER", test(SOCKET_NETNS)).replace("ICMPFILTER", test(ICMP_NETNS))
            .replace("FILTER", test("curtask->nsproxy->net_ns->ns.inum")).replace("PARENTS", PARENTS))


# --------------------------------------------------------------------------
# the log
# --------------------------------------------------------------------------
CAPPED = ("net", "tcp_state", "tcp_error")


class RowLog:
    """The rows on a line-buffered text stream. Past max_rows an ordinary net, tcp_state or tcp_error row is counted
    and dropped; a control's row and every other row always pass."""

    def __init__(self, stream, max_rows: int) -> None:
        self.stream, self.max_rows = stream, max_rows
        self.seen = self.dropped = self.lost = 0

    def write(self, row: str, **fields) -> None:
        self.seen += 1
        if row in CAPPED and "control" not in fields and self.seen > self.max_rows:
            self.dropped += 1
            return
        self.stream.write(json.dumps({"seq": self.seen, "t": round(time.time(), 6), "row": row, **fields}) + "\n")


# --------------------------------------------------------------------------
# what a row says of an address
# --------------------------------------------------------------------------
def plain_ip(ip: str) -> str:
    """An IPv4 address mapped into IPv6 as IPv4, so both ends of a loopback connection name it alike."""
    return ip[7:] if ip.startswith("::ffff:") and "." in ip else ip


def proto_of(family: int, kind: int) -> str:
    if family == 1:
        return "unix"
    return {1: "tcp", 2: "udp"}.get(kind, "raw")


def result_of(op: str, ret: int) -> str:
    """ok, or the errno's name of a failed call. A send returns its bytes."""
    if ret >= 0:
        return "ok"
    return errno.errorcode.get(-ret, str(ret))


def kernel_event(data: list) -> dict | None:
    """One socket call's print of TRACE as a row's fields, or None for an exec or a state print (Monitor.event reads
    those). The kernel's own uid stays as kernel_uid: who's uid is read from /proc. parents holds the caller's parent
    and grandparent."""
    name, nsecs, pid, tid, uid, netns, kind, family, a, port_a, b, port_b, ret, comm, sk, ppid, gppid = data
    if name in ("exec", "state", "error"):
        return None
    op = EVENTS[name]
    kind &= 0xF                                 # sock->type holds SOCK_NONBLOCK and SOCK_CLOEXEC above the type
    unix = name.startswith("unix_")
    if unix:
        local = ("@" + a) if port_a and a else a
        remote = ("@" + b) if port_b and b else b
        addresses = {"laddr": local or None, "lport": None, "raddr": remote or None, "rport": None}
    else:
        addresses = {"laddr": plain_ip(a) if a else None, "lport": port_a,
                     "raddr": plain_ip(b) if b and op not in ("bind", "listen") else None,
                     "rport": port_b if op not in ("bind", "listen") else None}
    return {"op": op, "proto": proto_of(family, kind), "family": FAMILIES.get(family, str(family)),
            "type": TYPES.get(kind, str(kind)), "netns": netns, "pid": pid, "tid": tid, "kernel_uid": uid,
            "comm": comm, **addresses, "result": result_of(op, ret), "sk": int(sk) or None,
            "parents": [ppid, gppid],
            "nsecs": nsecs}


def state_event(data: list) -> dict:
    """A state print of TRACE as a tcp_state row's fields: the socket, its ports, the state it left and the one it
    took, and the error the kernel set on it (None for none)."""
    _, nsecs, _, _, _, netns, old, new, _, sport, _, dport, err, _, sk, _, _ = data
    return {"netns": netns, "sk": int(sk), "lport": sport, "rport": dport, "old": TCP_STATES.get(old, str(old)),
            "new": TCP_STATES.get(new, str(new)), "error": errno.errorcode.get(err, str(err)) if err else None,
            "nsecs": nsecs}


def error_event(data: list) -> dict:
    """An error print of TRACE as a tcp_error row's fields: the socket, its ports and the errno's name."""
    _, nsecs, _, _, _, netns, _, _, _, sport, _, dport, err, _, sk, _, _ = data
    return {"netns": netns, "sk": int(sk), "lport": sport, "rport": dport,
            "error": errno.errorcode.get(err, str(err)), "nsecs": nsecs}


# --------------------------------------------------------------------------
# the socket tables (procnet, and the start of both methods)
# --------------------------------------------------------------------------
def hex_ip(text: str) -> str:
    """/proc/net/tcp's address: four bytes per word, each word in host order (little endian here)."""
    raw = bytes.fromhex(text)
    swapped = b"".join(raw[i:i + 4][::-1] for i in range(0, len(raw), 4))
    if len(swapped) == 4:
        return ".".join(str(byte) for byte in swapped)
    return plain_ip(str(ipaddress.IPv6Address(swapped)))


def inet_table(text: str, table: str) -> list[dict]:
    """The sockets of one of /proc/net/{tcp,tcp6,udp,udp6}: proto, family, state, addresses, uid and inode."""
    out = []
    for line in text.splitlines()[1:]:
        f = line.split()
        if len(f) < 10:
            continue
        local, remote = f[1].split(":"), f[2].split(":")
        out.append({"proto": table.rstrip("6"), "family": "inet6" if table.endswith("6") else "inet",
                    "type": "stream" if table.startswith("tcp") else "dgram", "state": f[3],
                    "laddr": hex_ip(local[0]), "lport": int(local[1], 16), "raddr": hex_ip(remote[0]),
                    "rport": int(remote[1], 16), "table_uid": int(f[7]), "inode": int(f[9])})
    return out


def unix_table(text: str) -> list[dict]:
    """/proc/net/unix's sockets that have a path: listening, connected and bound alike."""
    out = []
    for line in text.splitlines()[1:]:
        f = line.split(None, 7)
        if len(f) < 8:
            continue
        flags, kind, state, inode, path = int(f[3], 16), int(f[4], 16), f[5], int(f[6]), f[7]
        out.append({"proto": "unix", "family": "unix", "type": TYPES.get(kind, str(kind)), "state": state,
                    "listening": bool(flags & UNIX_LISTENING), "laddr": path, "inode": inode})
    return out


def classify(sock: dict, listening: set[int]) -> str | None:
    """A socket's op as a read of the tables shows it: listen, accept (a connection on a port something listens on),
    connect, bind (bound, neither listening nor connected), or None for nothing worth a row."""
    if sock["proto"] == "unix":
        if sock["listening"]:
            return "listen"
        return "accept" if sock["state"] == UNIX_CONNECTED else "bind"
    if sock["proto"] == "tcp":
        if sock["state"] == TCP_LISTEN:
            return "listen"
        if sock["state"] == TCP_CLOSE:
            return "bind" if sock["lport"] else None
        return "accept" if sock["lport"] in listening else "connect"
    if sock["rport"]:
        return "connect"
    return "bind" if sock["lport"] else None


def tables(init: int) -> list[dict]:
    """Every socket of the network namespace of process `init`, inet and unix, from its /proc/<pid>/net."""
    out = []
    for table in ("tcp", "tcp6", "udp", "udp6"):
        raw = read_bytes(f"/proc/{init}/net/{table}")
        if raw:
            out += inet_table(raw.decode(), table)
    raw = read_bytes(f"/proc/{init}/net/unix")
    if raw:
        out += unix_table(raw.decode("utf-8", "replace"))
    return out


def socket_owners(namespaces: dict[str, int]) -> dict[tuple[int, int], int]:
    """(netns inode, socket inode) -> the lowest pid that holds the socket, over every process in these namespaces."""
    wanted = {f"net:[{inode}]": inode for inode in namespaces.values()}
    owners: dict[tuple[int, int], int] = {}
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        space = wanted.get(link(f"/proc/{name}/ns/net") or "")
        if space is None:
            continue
        try:
            fds = os.listdir(f"/proc/{name}/fd")
        except OSError:
            continue
        for fd in fds:
            target = link(f"/proc/{name}/fd/{fd}") or ""
            if target.startswith("socket:["):
                key = (space, int(target[8:-1]))
                owners[key] = min(owners.get(key, int(name)), int(name))
    return owners


# --------------------------------------------------------------------------
# the monitor
# --------------------------------------------------------------------------
class Monitor:
    """One plan's machines, their network namespaces and the rows of their sockets."""

    def __init__(self, plan: dict, log: RowLog) -> None:
        self.log, self.poll_s = log, plan.get("poll_s", 0.25)
        self.nonces = {name: nonce.encode() for name, nonce in plan["nonces"].items()}
        self.machines = {name: {**machine, "down": False} for name, machine in plan["machines"].items()}
        self.netns: dict[int, list[str]] = {}
        for name, machine in self.machines.items():
            inode = self.namespace_of(machine["pid"])
            if inode is None:
                raise RuntimeError(f"no network namespace for {name} (pid {machine['pid']})")
            machine["netns"] = inode
            self.netns.setdefault(inode, []).append(name)
        self.execs: dict[int, str] = {}        # pid -> the file its last exec ran, from the trace
        self.walked: dict[int, tuple[list[str], dict]] = {}   # pid -> (chain, root) of its last walk (Monitor.walk)
        self.acked: set[int] = set()
        self.control_ops: dict[int, set[str]] = {}     # a control's pid -> the CONTROL_OPS written of it
        self.known: dict[tuple[int, int], dict] = {}   # procnet: (netns, socket inode) -> its last row's fields

    @staticmethod
    def namespace_of(pid: int) -> int | None:
        found = link(f"/proc/{pid}/ns/net")
        return int(found[5:-1]) if found and found.startswith("net:[") else None

    # ---------------------------------------------------------------- who
    def machine_of(self, pid: int | None, netns: int | None) -> str | None:
        """The service a process runs in, by its cgroup; else the one service of its network namespace."""
        cgroup = (read_bytes(f"/proc/{pid}/cgroup") or b"") if pid else b""
        found = next((name for name, m in self.machines.items() if m["container"].encode() in cgroup), None)
        if found:
            return found
        services = self.netns.get(netns or -1) or []
        return services[0] if len(services) == 1 else None

    def walk(self, pid: int) -> tuple[dict, list[str], dict] | None:
        """(status, chain, root) of a process still there, else None. Each walk is kept in walked, so a process that
        leaves later is still known by it. A zombie (exited, not yet reaped) keeps its status but no argv, so a walk
        whose process or root shows no argv is no walk: it would name a root by nothing."""
        for _ in range(WALKS):
            found = status(pid)
            walked = found and ancestry(pid, found)
            if walked and not (argv(pid) and walked[1]["cmd"]):
                return None
            if walked:
                self.walked.pop(pid, None)
                self.walked[pid] = walked
                if len(self.walked) > WALKED_MAX:
                    del self.walked[next(iter(self.walked))]
                return found, *walked
        return None

    def who(self, pid: int | None, parents: list[int] = ()) -> dict:
        """The process as the access log names an opener, read from /proc now. A row of the control names it (its
        SIGUSR1 comes from answer, once its rows are written). A process that left first is named through the nearest of
        `parents` (the kernel's parent and grandparent) still there: that ancestor's chain and root, with via its pid
        and the process's own uid, cmd and cwd unknown; an ancestor gone too stands by its last walk (walked, from an
        earlier row or exec of it), as does the process's own walk at its exec. A tool call's shell and its timeout
        leave as soon as the command ends, often before the row is read. With none of these it is gone."""
        walked = self.walk(pid) if pid else None
        if walked:
            found, chain, root = walked
            args = argv(pid)
            control = next((name for name, nonce in self.nonces.items() if nonce in args), None)
            return {"uid": found["uid"], "exe": link(f"/proc/{pid}/exe"), "cwd": link(f"/proc/{pid}/cwd"),
                    "cmd": shell_words(args, CMD_CHARS), "chain": chain, "root": root,
                    **({"control": control} if control else {})}
        exe = {"exe": self.execs[pid], "exe_from": "exec"} if pid in self.execs else {}
        for parent in parents:
            walked = self.walk(parent) if parent and parent > 1 else None
            if walked:
                _, chain, root = walked
                return {"exe": None, "cwd": None, "cmd": None, **exe, "chain": chain, "root": root, "via": parent}
        for known in (pid, *parents):
            if known and known > 1 and known in self.walked:
                chain, root = self.walked[known]
                return {"exe": None, "cwd": None, "cmd": None, **exe, "chain": chain, "root": root, "via": known,
                        "via_from": "earlier"}
        return {"gone": True, **exe}

    def write_net(self, fields: dict, pid: int | None) -> dict:
        """One net row: the event's fields, the machine and who. Returns the row's machine and who."""
        try:
            parents = fields.pop("parents", ())
            who = {"machine": self.machine_of(pid, fields.get("netns")), **self.who(pid, parents)}
            if "uid" not in who:
                who["uid"] = fields.get("kernel_uid")
            self.log.write("net", **{"pid": pid, **fields}, **who)
            if who.get("control"):
                self.answer(pid, fields.get("op"))
            return who
        except Exception as ex:  # noqa: BLE001 - one odd process must not void the run's log
            self.log.write("row_failed", pid=pid, error=repr(ex)[:300])
            return {"gone": True}

    def answer(self, pid: int, op: str | None) -> None:
        """A control's row of `op` is written: once each of CONTROL_OPS is, the control gets its SIGUSR1, once."""
        if op in CONTROL_OPS:
            self.control_ops.setdefault(pid, set()).add(op)
        if pid in self.acked or not set(CONTROL_OPS) <= self.control_ops.get(pid, set()):
            return
        self.acked.add(pid)
        self.control_ops.pop(pid, None)
        try:
            os.kill(pid, signal.SIGUSR1)
        except OSError:
            pass

    # ---------------------------------------------------------------- the tables
    def snapshot(self) -> dict[tuple[int, int], dict]:
        """Every socket of every namespace now, keyed by (netns, inode), with its op and its owner's pid."""
        reps = {inode: self.machines[names[0]]["pid"] for inode, names in self.netns.items()}
        owners = socket_owners({str(inode): inode for inode in reps})
        found = {}
        for inode, init in reps.items():
            socks = tables(init)
            listening = {s["lport"] for s in socks if s["proto"] == "tcp" and s["state"] == TCP_LISTEN}
            for sock in socks:
                op = classify(sock, listening)
                if op is None or sock["inode"] == 0:
                    continue
                fields = {"op": op, **{k: sock.get(k) for k in ("proto", "family", "type", "laddr", "lport")},
                          "raddr": sock.get("raddr") if op in ("connect", "accept") else None,
                          "rport": sock.get("rport") if op in ("connect", "accept") else None,
                          "netns": inode, "socket": sock["inode"], "result": "ok",
                          **({"tcp_state": TCP_STATES.get(int(sock["state"], 16), sock["state"])}
                             if sock["proto"] == "tcp" and op == "connect" else {})}
                found[(inode, sock["inode"])] = {"fields": fields, "pid": owners.get((inode, sock["inode"]))}
        return found

    def left_syn_sent(self, before: dict, after: dict | None) -> None:
        """A tcp_state row for a connect a read saw in SYN_SENT and the next read sees in another state, or gone (CLOSE,
        its error unknown)."""
        if before["fields"].get("tcp_state") != "SYN_SENT":
            return
        new = (after or {}).get("fields", {}).get("tcp_state", "CLOSE")
        if new == "SYN_SENT":
            return
        services = self.netns.get(before["fields"]["netns"]) or []
        self.log.write("tcp_state", netns=before["fields"]["netns"], socket=before["fields"]["socket"],
                       lport=before["fields"]["lport"], rport=before["fields"]["rport"], old="SYN_SENT", new=new,
                       error=None, machine=services[0] if len(services) == 1 else None, source="procnet")

    def table_rows(self, at_start: bool) -> None:
        """Rows for the sockets that appeared or changed op since the last read, and a close for those that went. A
        close names its socket's holder as the socket's last row did, with control dropped: the control's own rows
        are what proves it. A connect that leaves SYN_SENT gets a tcp_state row (left_syn_sent)."""
        now = self.snapshot()
        for key, sock in now.items():
            before = self.known.get(key)
            if before is not None:
                self.left_syn_sent(before, sock)
            if before is None or before["fields"]["op"] != sock["fields"]["op"]:
                extra = {"source": "procnet", **({"at_start": True} if at_start else {})}
                sock["who"] = self.write_net({**sock["fields"], **extra}, sock["pid"])
            else:
                sock["who"] = before["who"]
        if not at_start:
            for key, sock in self.known.items():
                if key not in now:
                    self.left_syn_sent(sock, None)
                    who = {k: v for k, v in sock["who"].items() if k != "control"}
                    self.log.write("net", **{"pid": sock["pid"], **sock["fields"], "op": "close",
                                             "source": "procnet"}, **who)
        self.known = now

    # ---------------------------------------------------------------- the poll
    def follow(self) -> None:
        """A machine under a new init has a new namespace the trace does not cover: a restart row, or machine_down."""
        for name, machine in self.machines.items():
            if machine["container"].encode() in (read_bytes(f"/proc/{machine['pid']}/cgroup") or b""):
                continue
            pid = find_init(machine["container"])
            if pid is None:
                if not machine["down"]:
                    machine["down"] = True
                    self.log.write("machine_down", machine=name)
                continue
            self.log.write("restart", machine=name, old_pid=machine["pid"], new_pid=pid)
            machine.update(pid=pid, down=False)

    # ---------------------------------------------------------------- the methods
    def run_procnet(self, stop: list) -> None:
        last = 0.0
        while not stop:
            if time.monotonic() - last >= self.poll_s:
                last = time.monotonic()
                self.follow()
                self.table_rows(at_start=False)
            time.sleep(min(0.05, self.poll_s))

    def event(self, line: str, clock: float) -> None:
        """One line of bpftrace's JSON output."""
        message = json.loads(line)
        kind = message.get("type")
        if kind == "lost_events":
            n = (message.get("data") or {}).get("events", 0)
            self.log.lost += n
            self.log.write("lost", events=n)
            return
        if kind != "value" or not isinstance(message.get("data"), list):
            return
        data = message["data"]
        if data[0] == "exec":
            self.execs[data[2]] = data[8]
            self.walk(data[2])                  # its chain and root while it is there, for a row after it left
            return
        if data[0] in ("state", "error"):
            fields = (state_event if data[0] == "state" else error_event)(data)
            nsecs = fields.pop("nsecs")
            services = self.netns.get(fields["netns"]) or []
            self.log.write("tcp_state" if data[0] == "state" else "tcp_error", **fields,
                           machine=services[0] if len(services) == 1 else None, source="ebpf",
                           t_kernel=round(nsecs / 1e9 + clock, 6))
            return
        fields = kernel_event(data)
        nsecs = fields.pop("nsecs")
        self.write_net({**fields, "source": "ebpf", "t_kernel": round(nsecs / 1e9 + clock, 6)}, fields["pid"])

    def run_ebpf(self, tracer: subprocess.Popen, lines: queue.Queue, stop: list) -> None:
        clock = time.time() - time.monotonic()      # bpftrace's nsecs is CLOCK_MONOTONIC
        last = time.monotonic()
        while not stop:
            try:
                line = lines.get(timeout=self.poll_s)
            except queue.Empty:
                line = None
            if line is not None:
                try:
                    self.event(line, clock)
                except Exception as ex:  # noqa: BLE001
                    self.log.write("row_failed", pid=None, error=repr(ex)[:300])
            if tracer.poll() is not None and lines.empty():
                self.log.write("trace_failed", error=f"bpftrace exited {tracer.returncode}")
                return self.run_procnet(stop)
            if time.monotonic() - last >= self.poll_s:
                last = time.monotonic()
                self.follow()
        tracer.send_signal(signal.SIGINT)
        try:
            tracer.wait(STOP_S)
        except subprocess.TimeoutExpired:
            tracer.kill()
        while True:
            try:
                line = lines.get(timeout=1)
            except queue.Empty:
                break
            if line is None:
                break
            try:
                self.event(line, clock)
            except Exception as ex:  # noqa: BLE001
                self.log.write("row_failed", pid=None, error=repr(ex)[:300])


def start_tracer(script_path: str, binary: str) -> tuple[subprocess.Popen, queue.Queue, int]:
    """bpftrace on the script, its stdout lines on a queue, once it has attached: (process, lines, probes). Raises with
    its stderr when it exits or does not attach in ATTACH_S."""
    tracer = subprocess.Popen([binary, "-f", "json", script_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, bufsize=1)
    lines: queue.Queue = queue.Queue()

    def pump():
        for line in tracer.stdout:
            lines.put(line)
        lines.put(None)
    threading.Thread(target=pump, daemon=True).start()
    errors: list[str] = []
    threading.Thread(target=lambda: errors.extend(tracer.stderr), daemon=True).start()
    deadline = time.monotonic() + ATTACH_S
    while time.monotonic() < deadline:
        try:
            line = lines.get(timeout=0.2)
        except queue.Empty:
            continue
        if line is None:
            break
        message = json.loads(line)
        if message.get("type") == "attached_probes":
            return tracer, lines, (message.get("data") or {}).get("probes", message.get("count"))
    tracer.kill()
    error = " ".join(line.strip() for line in errors if "MEMLOCK" not in line)[:600]
    raise RuntimeError(error or f"bpftrace did not attach in {ATTACH_S} s")


def main() -> None:
    plan = json.load(sys.stdin)
    stop: list = []
    signal.signal(signal.SIGTERM, lambda *_: stop.append(True))
    with open(plan["log"], "a", buffering=1) as stream:
        log = RowLog(stream, plan["max_rows"])
        monitor = Monitor(plan, log)
        tracer = None
        binaries = sorted(glob.glob(TRACER_GLOB))
        if plan.get("method", "auto") == "auto" and binaries:
            script_path = os.path.join(os.path.dirname(plan["log"]), "network.bt")
            with open(script_path, "w") as f:
                f.write(trace_script(list(monitor.netns)))
            try:
                tracer, lines, probes = start_tracer(script_path, binaries[0])
            except Exception as ex:  # noqa: BLE001 - the tables still work
                log.write("trace_failed", error=str(ex)[:600])
        elif plan.get("method", "auto") == "auto":
            log.write("trace_failed", error="no tracer mounted")
        monitor.table_rows(at_start=True)
        netns = {str(inode): names for inode, names in monitor.netns.items()}
        if tracer is not None:
            log.write("ready", pid=os.getpid(), method="ebpf", netns=netns, probes=probes)
            monitor.run_ebpf(tracer, lines, stop)
        else:
            log.write("ready", pid=os.getpid(), method="procnet", netns=netns)
            monitor.run_procnet(stop)
        log.write("stopped", rows=log.seen, dropped=log.dropped, lost=log.lost)


if __name__ == "__main__":
    main()
