#!/usr/bin/env bash
# Watch the boxes: scripts/box/watch.sh [--once] [--every MINUTES] [<instance-id> ...]
#
# Every few minutes (3 by default), for each box (the given ids, else every registered box not destroyed): rsync its
# runs/, logs/ and out/ down into this repository's with --update, then print what box_runner.py status says on the
# box (runs finished, running, failed, hung, lost), prefixed with the time and the box. A box that cannot be reached
# or reports trouble gets a line starting with !!. One box failing never stops the watch of the others. The watch
# only reads and copies: it never starts, stops or destroys anything. --once makes one pass and exits nonzero on any
# trouble.
set -uo pipefail
here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/../.." && pwd)
cd "$root" || exit 1
once=0
every=3
while [ $# -gt 0 ]; do
  case $1 in
    --once) once=1; shift ;;
    --every) every=$2; shift 2 ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) break ;;
  esac
done
if [ $# -gt 0 ]; then
  ids=("$@")
else
  read -r -d '' -a ids < <(uv run python -m scripts.box.fleet ids) || true
fi
if [ ${#ids[@]} -eq 0 ]; then
  echo "no boxes to watch"
  exit 1
fi

# prefix TEXT: print standard input with TEXT before each line.
prefix() {
  local line
  while IFS= read -r line; do
    printf '%s%s\n' "$1" "$line"
  done
}

watch_box() {
  local id=$1 config="$root/logs/boxes/$1/ssh_config" dir status
  if [ ! -f "$config" ]; then
    echo "!! $(date -u +%H:%M:%S) $id: no ssh config; run uv run python -m scripts.box.fleet status"
    return 1
  fi
  for dir in runs logs out; do
    mkdir -p "$root/$dir"
    rsync -a --update --timeout=300 -e "ssh -F $config" "box:/root/work/$dir/" "$root/$dir/" 2>&1 \
      | grep -v 'No such file or directory' | prefix "   $id rsync $dir: "
  done
  if ! status=$("$here/box_ssh.sh" "$id" 'cd /root/work && .venv/bin/python -m scripts.box.box_runner status' 2>&1); then
    printf '%s\n' "$status" | prefix "!! $(date -u +%H:%M:%S) $id: "
    return 1
  fi
  printf '%s\n' "$status" | prefix "   $(date -u +%H:%M:%S) $id: "
}

while true; do
  trouble=0
  for id in "${ids[@]}"; do
    watch_box "$id" || trouble=1
  done
  if [ "$once" -eq 1 ]; then
    exit "$trouble"
  fi
  sleep $((every * 60))
done
