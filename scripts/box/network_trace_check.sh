#!/usr/bin/env bash
# Which way of recording the agents' network calls works on this Docker host: network_trace_check.sh [options]
#
#   --image IMG      the agents' image to test in (default cbtask:python311); it needs python3 and bash
#   --bpftrace FILE  a bpftrace static AppImage already on this machine (default: download the pinned release into
#                    --cache, sha256 checked)
#   --cache DIR      where the download goes (default ${XDG_CACHE_HOME:-$HOME/.cache}/network_trace_check)
#   --json FILE      also write the report as JSON
#
# Run it on a box as root after box_kernel.sh, or on the Mac against Colima. It rents nothing and calls no model.
# It starts two containers from IMG and removes them at exit:
#   target  network none, like an agent's machine: a server as uid 1001, a client as the observer uid 3001
#   helper  the access-log sidecar's shape: the host's pid namespace, SYS_ADMIN, SYS_PTRACE, no network, and
#           /sys/kernel/tracing and /boot bound read-only
# and tests in order:
#   ebpf      bpftrace (fexit on inet_listen, inet_stream_connect, inet_csk_accept) in the helper, filtered to the
#             target's network namespace: works when it names the listen by 1001, the connect by 3001 and the accept
#             by 1001 with the client's port
#   kprobe    the same kernel functions by kprobe, attach only: the fallback when fexit is refused
#   procnet   the target's /proc/<pid>/net/{tcp,tcp6,udp,udp6,unix} and its processes' fds, standard library only:
#             works when it names the listener's and the client's pid and uid
#   ss        `nsenter -n ss -tulpanx` in the target's namespace, when the helper image has ss
#   conntrack `conntrack -L` in the target's namespace, when the helper image has conntrack, reported as info only.
#             Its events need NET_ADMIN and a conntrack rule in the agent's own namespace, and name no pid or uid
# The last line names the method to use: ebpf, else procnet (snapshots around each agent step). Exit 0 when one works,
# 1 when none does, 2 when the check cannot start.
set -euo pipefail

BPFTRACE_VERSION=v0.27.1
pinned_sha256() {  # the release's own digests (GitHub's asset digest field)
  case $1 in
    x86_64) echo c8d570302725bd1a3ed2f23bfa8d83dd78bde95daa98856279343052db5799c9 ;;
    aarch64) echo ad159222808d799eaa85424001535836314cf21977a71686b7b7c091b5195672 ;;
  esac
}
SERVER_UID=1001
OBSERVER_UID=3001
PORT=8123

image=cbtask:python311
bpftrace_file=
cache=${XDG_CACHE_HOME:-$HOME/.cache}/network_trace_check
json_out=
while [ $# -gt 0 ]; do
  case $1 in
    --image) image=$2; shift 2 ;;
    --bpftrace) bpftrace_file=$2; shift 2 ;;
    --cache) cache=$2; shift 2 ;;
    --json) json_out=$2; shift 2 ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
done

docker info > /dev/null 2>&1 || { echo "refused: no Docker daemon answers" >&2; exit 2; }
docker image inspect "$image" > /dev/null 2>&1 || { echo "refused: no image $image on this host" >&2; exit 2; }

run_id=$$-$RANDOM
target=nettrace-check-target-$run_id
helper=nettrace-check-helper-$run_id
cleanup() { docker rm -f "$target" "$helper" > /dev/null 2>&1 || true; }
trap cleanup EXIT

# bash 3.2 (macOS) has no associative arrays: result_<method> and detail_<method>.
report() { printf -v "result_$1" '%s' "$2"; printf -v "detail_$1" '%s' "$3"; printf '%-10s %-13s %s\n' "$1" "$2" "$3"; }
result_of() { local name=result_$1; echo "${!name:-}"; }
detail_of() { local name=detail_$1; echo "${!name:-}"; }

docker run -d --name "$target" --label network_trace_check=1 --network none --init --memory 256m \
  --entrypoint tail "$image" -f /dev/null > /dev/null
docker run -d --name "$helper" --label network_trace_check=1 --network none --pid host --memory 768m \
  --cap-add SYS_ADMIN --cap-add SYS_PTRACE \
  -v /sys/kernel/tracing:/sys/kernel/tracing:ro -v /boot:/host-boot:ro \
  --entrypoint tail "$image" -f /dev/null > /dev/null
pid=$(docker inspect -f '{{.State.Pid}}' "$target")
netns=$(docker exec "$helper" readlink "/proc/$pid/ns/net" | tr -dc 0-9)
[ -n "$netns" ] || { echo "refused: the helper cannot see the target's network namespace" >&2; exit 1; }

# ---------------------------------------------------------------- the kernel
kernel=$(docker exec "$helper" uname -r)
arch=$(docker exec "$helper" uname -m)
facts=$(docker exec -i "$helper" bash -s "$kernel" <<'EOF'
config=/host-boot/config-$1
for option in CONFIG_BPF_SYSCALL CONFIG_BPF_JIT CONFIG_DEBUG_INFO_BTF CONFIG_FTRACE_SYSCALLS CONFIG_KPROBES \
              CONFIG_FANOTIFY_ACCESS_PERMISSIONS CONFIG_NF_CONNTRACK_EVENTS; do
  if [ -r "$config" ]; then
    value=$(grep -E "^$option=" "$config" | cut -d= -f2); echo "$option=${value:-n}"
  else
    echo "$option=unknown"
  fi
done
[ -r /sys/kernel/btf/vmlinux ] && echo btf=yes || echo btf=no
for event in syscalls/sys_enter_connect syscalls/sys_enter_accept4 syscalls/sys_enter_bind syscalls/sys_enter_listen \
             sock/inet_sock_set_state; do
  [ -d "/sys/kernel/tracing/events/$event" ] && echo "tp:$event=yes" || echo "tp:$event=no"
done
EOF
)
echo "kernel     $kernel $arch"
echo "$facts" | tr '\n' ' ' | fold -s -w 118 | sed 's/^/           /'
echo

# ---------------------------------------------------------------- the target's server and client
start_server() {  # a listener as SERVER_UID that accepts once and holds the connection
  docker exec -d -u "$SERVER_UID" "$target" python3 -c "
import socket, time
s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(('127.0.0.1', $PORT)); s.listen(); c, _ = s.accept(); time.sleep(20)"
  for _ in $(seq 1 50); do
    docker exec "$target" python3 -c "
import sys; sys.exit(0 if any(l.split()[1].endswith(':%04X' % $PORT) and l.split()[3] == '0A'
                             for l in open('/proc/net/tcp').readlines()[1:]) else 1)" && return 0
    sleep 0.1
  done
  return 1
}
start_client() {  # a connect as OBSERVER_UID that holds the connection for $1 seconds
  docker exec -d -u "$OBSERVER_UID" "$target" python3 -c "
import socket, time
c = socket.create_connection(('127.0.0.1', $PORT)); time.sleep($1)"
}
stop_target_processes() {
  docker exec "$target" bash -c 'for p in /proc/[0-9]*; do u=$(stat -c %u $p); n=${p#/proc/};
    [ "$n" != 1 ] && [ "$u" != 0 ] && kill -9 "$n" 2>/dev/null; done; true'
}

# ---------------------------------------------------------------- ebpf and kprobe
bpftrace_bin=
if [ -z "$bpftrace_file" ]; then
  bpftrace_file=$cache/bpftrace-$BPFTRACE_VERSION-$arch
  if [ ! -s "$bpftrace_file" ] && [ -n "$(pinned_sha256 "$arch")" ]; then
    mkdir -p "$cache"
    if curl -fsSL -o "$bpftrace_file.part" \
        "https://github.com/bpftrace/bpftrace/releases/download/$BPFTRACE_VERSION/bpftrace-$arch"; then
      mv "$bpftrace_file.part" "$bpftrace_file"
    else
      rm -f "$bpftrace_file.part"
    fi
  fi
fi
if [ ! -s "$bpftrace_file" ]; then
  report ebpf "not tested" "no bpftrace AppImage for $arch (give --bpftrace FILE)"
  report kprobe "not tested" "no bpftrace"
else
  sum=$( (sha256sum "$bpftrace_file" 2>/dev/null || shasum -a 256 "$bpftrace_file") | cut -d' ' -f1)
  expected=$(pinned_sha256 "$arch")
  if [ -n "$expected" ] && [ "$sum" != "$expected" ]; then
    report ebpf "not tested" "$bpftrace_file has sha256 $sum, not the pinned $BPFTRACE_VERSION's"
    report kprobe "not tested" "no trusted bpftrace"
  else
    # The release is a nix-built AppImage. Its own runner mounts a tmpfs, which Docker's AppArmor profile refuses, so
    # the helper unpacks it and runs the store's binary through /nix.
    docker cp "$bpftrace_file" "$helper:/opt/bpftrace.AppImage" > /dev/null
    bpftrace_bin=$(docker exec -w /opt "$helper" bash -c 'chmod +x bpftrace.AppImage &&
      ./bpftrace.AppImage --appimage-extract > /dev/null && rm bpftrace.AppImage && [ ! -e /nix ] &&
      ln -s /opt/squashfs-root/nix /nix && readlink /opt/squashfs-root/entrypoint') || bpftrace_bin=
  fi
fi

if [ -n "$bpftrace_bin" ]; then
  version=$(docker exec "$helper" "$bpftrace_bin" --version 2>&1 | head -1)
  docker exec -i "$helper" bash -c 'cat > /opt/check.bt' <<EOF
fexit:vmlinux:inet_listen /curtask->nsproxy->net_ns->ns.inum == $netns/ {
  printf("listen %d %d %u %d\n", pid, uid, curtask->nsproxy->net_ns->ns.inum, args.sock->sk->__sk_common.skc_num);
}
fexit:vmlinux:inet_stream_connect /curtask->nsproxy->net_ns->ns.inum == $netns/ {
  printf("connect %d %d %u %d %d\n", pid, uid, curtask->nsproxy->net_ns->ns.inum,
         args.sock->sk->__sk_common.skc_num, bswap(((struct sockaddr_in *)args.uaddr)->sin_port));
}
fexit:vmlinux:inet_csk_accept /retval != 0 && curtask->nsproxy->net_ns->ns.inum == $netns/ {
  printf("accept %d %d %u %d %d\n", pid, uid, curtask->nsproxy->net_ns->ns.inum,
         retval->__sk_common.skc_num, bswap(retval->__sk_common.skc_dport));
}
EOF
  docker exec -d "$helper" bash -c "echo \$\$ > /opt/bpftrace.pid; exec $bpftrace_bin -f json /opt/check.bt > /opt/trace.jsonl 2> /opt/trace.err"
  attached=no
  for _ in $(seq 1 300); do
    if docker exec "$helper" grep -qs attached_probes /opt/trace.jsonl; then attached=yes; break; fi
    if docker exec "$helper" grep -qs ERROR /opt/trace.err; then break; fi
    sleep 0.1
  done
  if [ $attached = yes ]; then
    start_server && start_client 1 && sleep 1.5
    docker exec "$helper" bash -c 'p=$(cat /opt/bpftrace.pid); kill -INT "$p"; while [ -d "/proc/$p" ]; do sleep 0.1; done'

    verdict=$(docker exec -i "$helper" python3 - "$netns" "$SERVER_UID" "$OBSERVER_UID" "$PORT" <<'EOF'
import json, sys
netns, server, observer, port = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
rows = [json.loads(line) for line in open("/opt/trace.jsonl")]
events = [row["data"].split() for row in rows if row.get("type") == "printf"]
listen = [e for e in events if e[0] == "listen" and int(e[2]) == server and e[3] == netns and int(e[4]) == port]
connect = [e for e in events if e[0] == "connect" and int(e[2]) == observer and e[3] == netns and int(e[5]) == port]
accept = [e for e in events if e[0] == "accept" and int(e[2]) == server and e[3] == netns and int(e[4]) == port
          and connect and int(e[5]) == int(connect[0][4])]
lost = [row for row in rows if row.get("type") == "lost_events"]
if listen and connect and accept and not lost:
    print(f"works listen by uid {server} pid {listen[0][1]}, connect by uid {observer} pid {connect[0][1]} "
          f"from port {connect[0][4]}, accept by uid {server}")
else:
    print(f"fails listen={len(listen)} connect={len(connect)} accept={len(accept)} lost={len(lost)} rows={len(events)}")
EOF
)
    report ebpf "${verdict%% *}" "$version, fexit, ${verdict#* }"
  else
    report ebpf fails "$version: $(docker exec "$helper" grep -m1 ERROR /opt/trace.err 2>/dev/null || echo 'no attach in 30 s')"
  fi
  stop_target_processes
  kprobe=$(docker exec "$helper" timeout 20 "$bpftrace_bin" \
    -e 'kprobe:inet_listen, kprobe:inet_stream_connect, kretprobe:inet_csk_accept { } BEGIN { exit(); }' 2>&1 || true)
  case $kprobe in
    *ERROR*) report kprobe fails "$(echo "$kprobe" | grep -m1 ERROR)" ;;
    *Attach*) report kprobe works "$version, attach only" ;;
    *) report kprobe fails "$(echo "$kprobe" | head -1)" ;;
  esac
fi

# ---------------------------------------------------------------- procnet
start_server && start_client 15 && sleep 0.5
verdict=$(docker exec -i "$helper" python3 - "$pid" "$SERVER_UID" "$OBSERVER_UID" "$PORT" <<'EOF'
import os, sys, time
init, server, observer, port = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
started = time.perf_counter()
netns = os.readlink(f"/proc/{init}/ns/net")
owners = {}                                     # socket inode -> (pid, uid)
for entry in os.listdir("/proc"):
    if not entry.isdigit():
        continue
    try:
        if os.readlink(f"/proc/{entry}/ns/net") != netns:
            continue
        uid = os.stat(f"/proc/{entry}").st_uid
        for fd in os.listdir(f"/proc/{entry}/fd"):
            link = os.readlink(f"/proc/{entry}/fd/{fd}")
            if link.startswith("socket:["):
                owners[int(link[8:-1])] = (int(entry), uid)
    except OSError:
        continue
sockets = []
for table in ("tcp", "tcp6", "udp", "udp6"):
    for line in open(f"/proc/{init}/net/{table}").readlines()[1:]:
        f = line.split()
        sockets.append((table, int(f[1].split(":")[1], 16), int(f[2].split(":")[1], 16), f[3], int(f[7]), int(f[9])))
unix = len(open(f"/proc/{init}/net/unix").readlines()) - 1
ms = (time.perf_counter() - started) * 1000
listener = [s for s in sockets if s[0] == "tcp" and s[1] == port and s[3] == "0A" and s[4] == server]
client = [s for s in sockets if s[0] == "tcp" and s[2] == port and s[3] == "01" and s[4] == observer]
named = [owners.get(s[5]) for s in listener + client]
if listener and client and all(named):
    print(f"works listener uid {server} pid {named[0][0]}, client uid {observer} pid {named[1][0]}; "
          f"{len(sockets)} inet and {unix} unix sockets in {ms:.0f} ms")
else:
    print(f"fails listener={len(listener)} client={len(client)} named={sum(map(bool, named))}")
EOF
)
report procnet "${verdict%% *}" "${verdict#* }"

# ---------------------------------------------------------------- ss and conntrack
if docker exec "$helper" sh -c 'command -v ss && command -v nsenter' > /dev/null 2>&1; then
  ss_out=$(docker exec "$helper" nsenter -t "$pid" -n ss -tulpanxe 2>&1 || true)
  if echo "$ss_out" | grep -q "127.0.0.1:$PORT .*uid:$OBSERVER_UID" && echo "$ss_out" | grep -q "LISTEN.*:$PORT .*pid="; then
    report ss works "nsenter -t <pid> -n ss -tulpanxe names pid and uid"
  else
    report ss fails "$(echo "$ss_out" | head -1)"
  fi
else
  report ss "not tested" "no ss or nsenter in $image; procnet reads the same tables"
fi
if docker exec "$helper" sh -c 'command -v conntrack' > /dev/null 2>&1; then
  ct=$(docker exec "$helper" nsenter -t "$pid" -n conntrack -L 2>&1 | tail -1 || true)
  report conntrack info "$ct (needs NET_ADMIN and a conntrack rule in the agent's namespace; no pid or uid)"
else
  report conntrack "not tested" "no conntrack in $image; it would name no pid or uid"
fi
stop_target_processes

# ---------------------------------------------------------------- the method
if [ "$(result_of ebpf)" = works ]; then
  method=ebpf
elif [ "$(result_of procnet)" = works ]; then
  method=procnet
else
  method=none
fi
echo
echo "method     $method"
if [ -n "$json_out" ]; then
  {
    printf '{"kernel": "%s", "arch": "%s", "image": "%s", "method": "%s", "facts": {' "$kernel" "$arch" "$image" "$method"
    echo "$facts" | awk -F= 'NR > 1 { printf ", " } { printf "\"%s\": \"%s\"", $1, $2 }'
    printf '}, "results": {'
    first=1
    for name in ebpf kprobe procnet ss conntrack; do
      [ -n "$(result_of $name)" ] || continue
      [ $first = 1 ] || printf ', '
      first=0
      text=$(detail_of $name); text=${text//\\/\\\\}
      printf '"%s": {"result": "%s", "detail": "%s"}' "$name" "$(result_of $name)" "${text//\"/\\\"}"
    done
    printf '}}\n'
  } > "$json_out"
fi
[ $method != none ]
