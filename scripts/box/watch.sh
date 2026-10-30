#!/usr/bin/env bash
# Watch the boxes: scripts/box/watch.sh [--once] [--every MINUTES] [<instance-id> ...]
#
# Every few minutes (3 by default), for each box (the given ids, else every registered box not destroyed): rsync its
# runs/, logs/ and out/ down into this repository's with --update, and its /root/screen.log, /root/setup.log and
# Inspect's data folder (traces, sample buffers) into logs/boxes/<id>/files/, then check that /root/box_id names the
# box and print what box_runner.py status says there (runs finished, running, failed, incomplete, hung, lost),
# prefixed with the time and the box. Each pass starts with what the watched boxes bill per hour. A box that cannot be
# reached, a failed rsync, a box that reports trouble, and a box whose runs all ended (it bills until it is captured
# and destroyed) get a line starting with !!. One box failing never stops the watch of the others. The watch only
# reads and copies: it never starts, stops or destroys anything. --once makes one pass and exits nonzero on any !!.
# rsync is Homebrew's: the Mac's /usr/bin/rsync is openrsync.
set -uo pipefail
here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/../.." && pwd)
cd "$root" || exit 1
once=0
every=3
while [ $# -gt 0 ]; do
  case $1 in
    --once) once=1; shift ;;
    --every) every=${2:-}; shift 2 || exit 2 ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) break ;;
  esac
done
case $every in
  ''|*[!0-9]*|0*) echo "--every takes whole minutes, 1 or more"; exit 2 ;;
esac
case $root in
  *[[:space:]]*) echo "!! $root holds whitespace, which rsync's -e cannot take"; exit 1 ;;
esac
rsync_bin=/opt/homebrew/bin/rsync
[ -x "$rsync_bin" ] || rsync_bin=$(command -v rsync || true)
version=$([ -n "$rsync_bin" ] && "$rsync_bin" --version 2> /dev/null)
case ${version%%$'\n'*} in
  *"version 3."*) ;;
  *) echo "!! no rsync 3 (the Mac's /usr/bin/rsync is openrsync): brew install rsync"; exit 1 ;;
esac

if ! listing=$(uv run python -m scripts.box.fleet ids --dph "$@" 2>&1); then
  printf '%s\n' "$listing" | sed 's/^/!! /'
  exit 1
fi
ids=()
dphs=()
total=0
while read -r first second; do
  if [ "$first" = total ]; then
    total=$second
  elif [ -n "$first" ]; then
    ids+=("$first")
    dphs+=("$second")
  fi
done <<< "$listing"
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

# pull ID WHAT ARGS...: one rsync from the box, its messages flagged; 0 when it succeeded (24: files vanished).
pull() {
  local id=$1 what=$2 code
  shift 2
  "$rsync_bin" -a --update --timeout=300 -e "ssh -F $root/logs/boxes/$id/ssh_config" "$@" 2>&1 \
    | prefix "!! $id rsync $what: "
  code=${PIPESTATUS[0]}
  if [ "$code" -ne 0 ] && [ "$code" -ne 24 ]; then
    echo "!! $(date -u +%H:%M:%S) $id: the rsync of $what exited $code"
    return 1
  fi
}

watch_box() {
  local id=$1 dph=$2 status trouble=0 qid
  qid=$(printf %q "$id")
  if [ ! -f "$root/logs/boxes/$id/ssh_config" ]; then
    echo "!! $(date -u +%H:%M:%S) $id: no ssh config; run uv run python -m scripts.box.fleet status"
    return 1
  fi
  # One rsync of the clone limited to runs/, logs/ and out/: a folder the box has not made yet is no error.
  pull "$id" outputs --include='/runs/***' --include='/logs/***' --include='/out/***' --exclude='*' \
    box:/root/work/ "$root/" || trouble=1
  mkdir -p "$root/logs/boxes/$id/files"
  pull "$id" "the screen's logs" --relative --ignore-missing-args box:/root/screen.log box:/root/setup.log \
    box:/root/.local/share/inspect_ai/ "$root/logs/boxes/$id/files/" || trouble=1
  if ! status=$("$here/box_ssh.sh" "$id" "[ \"\$(cat /root/box_id 2> /dev/null)\" = $qid ] \
|| { echo \"/root/box_id does not name $qid: the endpoint reaches another box\"; exit 3; }; \
cd /root/work && .venv/bin/python -m scripts.box.box_runner status" 2>&1); then
    printf '%s\n' "$status" | prefix "!! $(date -u +%H:%M:%S) $id: "
    trouble=1
  else
    printf '%s\n' "$status" | prefix "   $(date -u +%H:%M:%S) $id: "
  fi
  case $status in
    *"all runs ended"*)
      echo "!! $(date -u +%H:%M:%S) $id: all runs ended and the box still bills \$$dph/h: scripts/box/capture_check.sh" \
        "$id --pull, then uv run python -m scripts.box.fleet destroy $id"
      trouble=1 ;;
  esac
  return "$trouble"
}

while true; do
  echo "== $(date -u +%H:%M:%S) ${#ids[@]} boxes, \$$total/h by their offers"
  trouble=0
  i=0
  while [ "$i" -lt ${#ids[@]} ]; do
    watch_box "${ids[$i]}" "${dphs[$i]}" || trouble=1
    i=$((i + 1))
  done
  if [ "$once" -eq 1 ]; then
    exit "$trouble"
  fi
  sleep $((every * 60))
done
