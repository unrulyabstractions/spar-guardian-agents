#!/usr/bin/env bash
# The cloud box guard's progress adapter: .claude/box_progress.sh <provider> <instance id>
#
# Prints one line for ~/.claude/hooks/cloud_boxes_guard.py:
#   progress <seconds>   the launcher or a cell lives, and the runs last wrote this long ago (or the launcher started
#                        this long ago, when nothing was written since)
#   ended <why>          no launcher and no cell lives, or no clone on the box yet (the guard's setup grace covers a
#                        box that is still booting or setting up)
#   blocked <why>        the box's own OpenRouter key has no limit left: no run can progress
#   unreachable <why>    ssh failed, no ssh config could be written, or the box that answered names another id in
#                        /root/box_id (`wrong-box <name>`): its endpoint reaches another box, so nothing here may
#                        say `ended`, which would start a teardown against that other box
#   unknown              not a box of this repository (runs/boxes.json does not list it)
#
# scripts/box/box_probe.py goes to the box's python3 over ssh's standard input and reads /proc and the files there
# (probe); the same file turns its reading into the line here (verdict). A launcher that writes nothing for long reads
# as progress of a growing age, which the guard counts as idle. The key check runs on the box with the box's own key;
# only the remaining limit comes back. Reads only, except that it runs `fleet status` once to write a missing ssh config
# of a registered box, or to refresh the endpoint of a box whose endpoint answered with another box's id.
set -uo pipefail
provider=${1:-}
id=${2:-}
root=$(cd "$(dirname "$0")/.." && pwd)
probe=$root/scripts/box/box_probe.py
if [ "$provider" != vast ]; then
  echo "unknown"
  exit 0
fi
case $id in
  ''|*[!0-9]*) echo "unknown"; exit 0 ;;     # a vast.ai id is digits
esac
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
reading=$(ssh -F "$cfg" -o ConnectTimeout=20 -o ServerAliveInterval=15 -o ServerAliveCountMax=4 box \
  "python3 - probe --key" < "$probe" 2> /dev/null)
rc=$?
line=$(printf '%s\n' "$reading" | grep '^{' | tail -1)
if [ $rc -ne 0 ] || [ -z "$line" ]; then
  echo "unreachable ssh-rc-$rc"
  exit 0
fi
said=$(printf '%s\n' "$line" | python3 "$probe" verdict "$id" 2> /dev/null)
case $said in
  "unreachable wrong-box"*)
    # the endpoint reaches another box: refresh the ssh configs once, and never say `ended` for this id
    (cd "$root" && uv run python -m scripts.box.fleet status > /dev/null 2>&1) ;;
  '') said="unreachable unreadable-reading" ;;
esac
echo "$said"
