#!/usr/bin/env bash
# The cloud box guard's progress adapter: .claude/box_progress.sh <provider> <instance id>
#
# Prints one line for ~/.claude/hooks/cloud_boxes_guard.py:
#   progress <seconds>   the box's runner is alive with runs running, and Inspect last wrote this long ago
#   ended <why>          no runner, no run running, or no clone on the box yet (the guard's setup grace covers a
#                        box that is still booting or setting up)
#   blocked <why>        the box's own OpenRouter key has no limit left: no run can progress
#   unreachable <why>    ssh failed, or no ssh config could be written
#   unknown              not a box of this repository (runs/boxes.json does not list it)
#
# Progress is what Inspect writes while a run works: its trace files, the run folders and the eval logs. The
# runner's plan (logs/<study>/screen/box_*.json), which the runner rewrites every minute, and the screen logs do
# not count. The key check runs on the box with the box's own key; only the remaining limit comes back.
# Reads only, except that it runs `fleet status` once to write a missing ssh config of a registered box.
set -uo pipefail
provider=${1:-}
id=${2:-}
root=$(cd "$(dirname "$0")/.." && pwd)
if [ "$provider" != vast ]; then
  echo "unknown"
  exit 0
fi
if ! python3 - "$root/runs/boxes.json" "$id" <<'EOF'
import json, sys
try:
    boxes = json.load(open(sys.argv[1]))["boxes"]
except Exception:
    sys.exit(1)
sys.exit(0 if any(str(b.get("id")) == sys.argv[2] and not b.get("destroyed") for b in boxes) else 1)
EOF
then
  echo "unknown"
  exit 0
fi
cfg="$root/logs/boxes/$id/ssh_config"
if [ ! -f "$cfg" ]; then
  (cd "$root" && uv run python -m scripts.box.fleet status > /dev/null 2>&1)
  if [ ! -f "$cfg" ]; then
    echo "unreachable no-ssh-config"
    exit 0
  fi
fi
out=$(ssh -F "$cfg" -o ConnectTimeout=20 -o ServerAliveInterval=15 -o ServerAliveCountMax=4 box 'bash -s' 2>/dev/null <<'REMOTE'
if ! cd /root/work 2>/dev/null; then echo "NOWORK"; exit 0; fi
alive=$(python3 - <<'PY'
# the runner's processes, read from /proc: no pgrep needed, and this script's own command line never matches
import os
n = 0
for pid in filter(str.isdigit, os.listdir("/proc")):
    try:
        args = open(f"/proc/{pid}/cmdline", "rb").read().split(b"\0")
    except OSError:
        continue
    args = [a.decode(errors="replace") for a in args if a]
    if "scripts.box.box_runner" in " ".join(args) and "run" in args and int(pid) != os.getpid():
        n += 1
print(n)
PY
)
remaining=$(python3 - <<'PY'
import json, re, urllib.request
key = None
try:
    for line in open("/root/.box_env"):
        m = re.match(r"\s*(export\s+)?[A-Z_]*OPENROUTER_API_KEY=['\"]?([^'\"\s]+)", line)
        if m:
            key = m.group(2)
except OSError:
    pass
if not key:
    print("nokey")
    raise SystemExit
try:
    req = urllib.request.Request("https://openrouter.ai/api/v1/key", headers={"Authorization": "Bearer " + key})
    d = json.load(urllib.request.urlopen(req, timeout=20))["data"]
    r = d.get("limit_remaining")
    print("inf" if r is None else r)
except Exception:
    print("err")
PY
)
newest=$(find /root/.local/share/inspect_ai/traces /root/work/runs /root/work/logs -type f \
  ! -path '*/screen/*' -printf '%T@\n' 2>/dev/null | sort -n | tail -1)
running=$(.venv/bin/python -m scripts.box.box_runner status 2>/dev/null | grep -o '[0-9][0-9]* running' \
  | awk '{s += $1} END {print s + 0}')
echo "ALIVE=$alive RUNNING=${running:-0} NEWEST=${newest%.*} NOW=$(date +%s) KEY=$remaining"
REMOTE
)
rc=$?
line=$(printf '%s\n' "$out" | grep -E '^(NOWORK|ALIVE=)' | tail -1)
if [ $rc -ne 0 ] || [ -z "$line" ]; then
  echo "unreachable ssh-rc-$rc"
  exit 0
fi
if [ "$line" = NOWORK ]; then
  echo "ended no-clone-on-the-box"
  exit 0
fi
field() { printf '%s\n' "$line" | tr ' ' '\n' | sed -n "s/^$1=//p"; }
alive=$(field ALIVE); running=$(field RUNNING); newest=$(field NEWEST); now=$(field NOW); key=$(field KEY)
case $key in
  ''|nokey|err|inf) ;;
  *) if python3 -c "import sys; sys.exit(0 if float(sys.argv[1]) <= 0 else 1)" "$key" 2>/dev/null; then
       echo "blocked the-box-key-has-no-limit-left"
       exit 0
     fi ;;
esac
if [ "${alive:-0}" -eq 0 ] || [ "${running:-0}" -eq 0 ]; then
  echo "ended runner-alive=${alive:-0} running=${running:-0}"
  exit 0
fi
if [ -n "$newest" ] && [ -n "$now" ]; then
  echo "progress $(( now - newest ))"
else
  echo "progress 0"
fi
