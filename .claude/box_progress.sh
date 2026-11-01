#!/usr/bin/env bash
# The cloud box guard's progress adapter: .claude/box_progress.sh <provider> <instance id>
#
# Prints one line for ~/.claude/hooks/cloud_boxes_guard.py:
#   progress <seconds>   the box's runner is alive, and its newest run or log file is this old
#   ended                the runner ended, or every run of its plan is finished or failed
#   blocked <why>        the study's model key is exhausted: no run can progress
#   unreachable          ssh failed
#   unknown              not a vast.ai box of this repository (no ssh config here)
# Reads only. Never starts, stops or destroys anything.
set -uo pipefail
provider=${1:-}
id=${2:-}
root=$(cd "$(dirname "$0")/.." && pwd)
cfg="$root/logs/boxes/$id/ssh_config"
if [ "$provider" != vast ] || [ ! -f "$cfg" ]; then
  echo "unknown"
  exit 0
fi
# the OpenRouter key's remaining limit, read with the key from a login zsh; the key is never printed
remaining=$(zsh -lic 'curl -s -m 20 -H "Authorization: Bearer $OPENROUTER_API_KEY" https://openrouter.ai/api/v1/key' 2>/dev/null \
  | python3 -c 'import json,sys
try:
    d=json.load(sys.stdin)["data"]; r=d.get("limit_remaining"); print("inf" if r is None else r)
except Exception: print("?")' 2>/dev/null)
case $remaining in
  '?'|inf) ;;
  *) if python3 -c "import sys; sys.exit(0 if float('$remaining') <= 0 else 1)" 2>/dev/null; then
       echo "blocked openrouter-key-limit-exhausted"
       exit 0
     fi ;;
esac
out=$(ssh -F "$cfg" -o ConnectTimeout=20 box 'cd /root/work 2>/dev/null || { echo NOWORK; exit 0; }
alive=$(pgrep -f "scripts.box.box_runner run" | wc -l)
newest=$(find /root/work/logs /root/work/runs -type f -newer /root/box_id -printf "%T@\n" 2>/dev/null | sort -n | tail -1)
[ -n "$newest" ] || newest=$(find /root/work/logs /root/work/runs -type f -printf "%T@\n" 2>/dev/null | sort -n | tail -1)
now=$(date +%s)
echo "ALIVE=$alive NEWEST=${newest%.*} NOW=$now"
.venv/bin/python -m scripts.box.box_runner status 2>/dev/null | head -20' 2>/dev/null)
rc=$?
if [ $rc -ne 0 ] || [ -z "$out" ]; then
  echo "unreachable"
  exit 0
fi
case $out in
  NOWORK*) echo "ended no-clone-on-the-box"; exit 0 ;;
esac
alive=$(printf '%s\n' "$out" | sed -n 's/^ALIVE=\([0-9]*\) .*/\1/p' | head -1)
newest=$(printf '%s\n' "$out" | sed -n 's/^ALIVE=[0-9]* NEWEST=\([0-9]*\) .*/\1/p' | head -1)
now=$(printf '%s\n' "$out" | sed -n 's/.*NOW=\([0-9]*\)$/\1/p' | head -1)
running=$(printf '%s\n' "$out" | grep -o '[0-9]* running' | head -1 | cut -d' ' -f1)
if [ "${alive:-0}" -eq 0 ] || [ "${running:-0}" -eq 0 ]; then
  echo "ended runner-alive=${alive:-0} running=${running:-0}"
  exit 0
fi
if [ -n "$newest" ] && [ -n "$now" ]; then
  echo "progress $(( now - newest ))"
else
  echo "progress 0"
fi
