#!/usr/bin/env bash
# Start a box's launcher: scripts/box/box_run.sh <instance-id> [--mock] [--main-key]
#
# The launcher is the Mac's own, scripts/study/run_study.py, on the box's plan: `run_study <plan> --yes [--gates
# <spec>] --only <the box's targets> --limit max_agents=<its agents at once>`, all from the box's registry entry and its
# pinned commit's plan through fleet.py run-args. That refuses a model that needs a key a box does not hold, a run of
# the box of which this machine holds a trace the box did not make, and a launcher of the study live on this machine.
# --mock passes --mock without --gates and with only the targets the pinned plan holds: every model is Inspect's mock
# model, the runs go to <study>_mock under names that end in _mock, no key goes to the box and nothing is paid. Running
# this again on a box whose launcher ended starts the runs not stored.
#
# A box holds the key variable OPENROUTER_API_KEY. It goes from this shell's environment to /root/.box_env (mode 600)
# through ssh's standard input: never on a command line, never printed. The box checks only its length. --main-key
# sends OPENROUTER_API_KEY itself, which every study's boxes take (CLAUDE.md §3a); without it the key sent is
# BOX_OPENROUTER_API_KEY, a key made for the launch. scripts/box/box_key.py refuses either unless OpenRouter caps it
# and enough is left for every run of every open box that holds the key at its dollar cap, and records that this box
# holds it.
#
# A box whose runs call Claude straight at Anthropic also holds the Anthropic key file: each key file the box's entry
# records (fleet.py entry key_files, a path under the home such as .config/spar-guardian-agents/anthropic_key) goes
# from this machine, its links resolved, to the same path under /root, and its workspace file (<key file>.workspace)
# with it when this machine has one; a workspace file the box holds from before is removed when this machine has none.
# A key file missing here refuses the run before any key is sent. Each goes through ssh's standard input to a .new
# file under umask 077, then mv: a regular file of mode 600, never on a command line, never printed. The box checks
# its mode and prints only its length.
#
# Before any key goes, the box must show /root/box_id naming this instance (the endpoint reaches it) and no launcher
# alive: a live launcher's runs read the key file and workspace file as each starts, so replacing them under it is
# refused. The launcher then starts under nohup setsid with /root/.box_env sourced, uv on its PATH and UV_NO_SYNC set
# (the cells' `uv run` uses the .venv the launch pinned as it is), after the same two checks again. Its output goes to
# /root/launcher.log and its pid to /root/launcher.pid.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/../.." && pwd)
cd "$root"
if [ $# -lt 1 ]; then
  sed -n '2,31p' "$0"
  exit 2
fi
id=$1
shift
mock=0
main_key=0
pass=()
for arg in "$@"; do
  case $arg in
    --main-key) main_key=1 ;;
    --mock) mock=1; pass+=("$arg") ;;
    *) echo "refused: box_run.sh takes --mock and --main-key, not $arg"; exit 2 ;;
  esac
done

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
uv run python -m scripts.box.fleet run-args "$id" ${pass[@]+"${pass[@]}"} > "$work/args"
launcher=()
while IFS= read -r line; do
  launcher+=("$line")
done < "$work/args"
name=$(uv run python -m scripts.box.fleet entry "$id" key)
key_list=$(uv run python -m scripts.box.fleet entry "$id" key_files)
key_files=()
IFS=, read -r -a key_files <<< "$key_list"
echo "== box $id: python -m ${launcher[*]}"

sent_from=BOX_$name
if [ "$main_key" -eq 1 ]; then
  sent_from=$name
fi

# check_box_key: OpenRouter must cap the key and enough must be left for the box's runs; only --main-key lets the main
# key pass. Nothing goes to the box.
check_box_key() {
  local check=(check "$name" --box-id "$id")
  if [ "$main_key" -eq 1 ]; then
    check+=(--main-key)
  fi
  uv run python -m scripts.box.box_key "${check[@]}" || exit 1
}

# send_key: the box's key into /root/.box_env, its length checked on the box; never printed.
send_key() {
  echo "== the key ($name, from $sent_from) into /root/.box_env"
  printf 'export %s=%q\n' "$name" "${!sent_from}" \
    | "$here/box_ssh.sh" "$id" 'umask 077 && trap "rm -f /root/.box_env.new" EXIT HUP INT TERM &&
cat > /root/.box_env.new && mv /root/.box_env.new /root/.box_env'
  # ssh joins its arguments into one command line for the box's shell, so each one is quoted for that shell first.
  "$here/box_ssh.sh" "$id" "bash -s -- $(printf %q "$name")" <<'REMOTE'
set -euo pipefail
# shellcheck source=/dev/null
. /root/.box_env
value=${!1:-}
if [ "${#value}" -lt 20 ]; then
  echo "!! $1 holds ${#value} characters on the box"
  exit 1
fi
echo "   $1 set, ${#value} characters; $(stat -c '%a %U' /root/.box_env)"
REMOTE
}

# local_file: the file a path under this machine's home names, its links resolved; refused when it is no file.
local_file() {
  local found
  found=$(realpath "$HOME/$1" 2> /dev/null || true)
  if [ -z "$found" ] || [ ! -f "$found" ]; then
    echo "refused: ~/$1 is no file on this machine" >&2
    return 1
  fi
  printf '%s\n' "$found"
}

# has_workspace: whether this machine has a workspace file (or a link) beside the key file.
has_workspace() { [ -e "$HOME/$1.workspace" ] || [ -L "$HOME/$1.workspace" ]; }

# check_key_files: every key file the box's runs need, and each workspace file here, is a file, before anything is sent.
check_key_files() {
  local rel
  for rel in ${key_files[@]+"${key_files[@]}"}; do
    local_file "$rel" > /dev/null
    if has_workspace "$rel"; then
      local_file "$rel.workspace" > /dev/null
    fi
  done
}

# send_key_file: a file under this machine's home to the same path under /root, a regular file of mode 600 with at least
# $2 characters; its length checked on the box, never printed.
send_key_file() {
  local rel=$1 least=$2 from target
  from=$(local_file "$rel")
  target=$(printf %q "/root/$rel")
  echo "== ~/$rel into /root/$rel"
  "$here/box_ssh.sh" "$id" "umask 077 && mkdir -p $(printf %q "$(dirname "/root/$rel")") &&
trap 'rm -f $target.new' EXIT HUP INT TERM && cat > $target.new && mv $target.new $target" < "$from"
  "$here/box_ssh.sh" "$id" "bash -s -- $(printf '%q ' "/root/$rel" "$least")" <<'REMOTE'
set -euo pipefail
if [ -L "$1" ] || [ ! -f "$1" ]; then
  echo "!! $1 is no regular file on the box"
  exit 1
fi
value=$(cat "$1")
mode=$(stat -c '%a' "$1")
if [ "$mode" != 600 ] || [ "${#value}" -lt "$2" ]; then
  echo "!! $1 is mode $mode with ${#value} characters on the box"
  exit 1
fi
echo "   $1 set, ${#value} characters; $(stat -c '%a %U' "$1")"
REMOTE
}

# send_key_files: each key file the box's runs need, then its workspace file, or the removal of the box's old one.
send_key_files() {
  local rel
  for rel in ${key_files[@]+"${key_files[@]}"}; do
    send_key_file "$rel" 20
    if has_workspace "$rel"; then
      send_key_file "$rel.workspace" 8
    else
      "$here/box_ssh.sh" "$id" "rm -f $(printf %q "/root/$rel.workspace")" < /dev/null
    fi
  done
}

# on_box: the box's side, run as `bash -s -- ready <id>` (the two checks alone) or `bash -s -- start <id> <launcher>`.
on_box() {
  cat <<'REMOTE'
set -euo pipefail
alive() { [ -f /root/launcher.pid ] && grep -qs scripts.study.run_study "/proc/$(cat /root/launcher.pid)/cmdline"; }
# ready: /root/box_id names this instance and no launcher is alive on the box.
ready() {
  if [ "$(cat /root/box_id 2> /dev/null || true)" != "$1" ]; then
    echo "refused: /root/box_id names $(cat /root/box_id 2> /dev/null || echo nothing), not $1: the endpoint" \
      "reaches another box, or box_setup.sh never ran"
    exit 1
  fi
  if alive; then
    echo "refused: a launcher is alive (pid $(cat /root/launcher.pid))"
    exit 1
  fi
}
start() {
  ready "$1"
  shift
  cd /root/work
  rm -f /root/launcher.pid
  echo "== $(date -u +%FT%TZ) box_run: $*" >> /root/launcher.log
  # shellcheck disable=SC2016  # the inner bash expands "$@" and $$
  nohup setsid bash -c 'export PATH=/root/work/.venv/bin:/root/.local/bin:$PATH UV_NO_SYNC=1; \
[ ! -f /root/.box_env ] || . /root/.box_env; echo $$ > /root/launcher.pid; exec .venv/bin/python -m "$@"' \
    launcher "$@" >> /root/launcher.log 2>&1 &
  local launched=$! waited
  for waited in $(seq 1 60); do
    if alive; then
      echo "   launcher pid $(cat /root/launcher.pid), started after ${waited} s"
      sleep 15
      tail -n 5 /root/launcher.log
      alive || { echo "!! the launcher ended at once"; exit 1; }
      exit 0
    fi
    kill -0 "$launched" 2> /dev/null || break
    sleep 1
  done
  echo "!! no launcher; the end of /root/launcher.log:"
  tail -n 30 /root/launcher.log
  exit 1
}
case $1 in
  ready) ready "$2" ;;
  start) shift; start "$@" < /dev/null ;;
esac
REMOTE
}

if [ "$mock" -eq 0 ]; then
  check_key_files
  check_box_key
fi
echo "== the box: its instance, and no launcher alive"
on_box | "$here/box_ssh.sh" "$id" "bash -s -- ready $(printf %q "$id")"
if [ "$mock" -eq 1 ]; then
  echo "== the mock model: no key goes to the box"
else
  send_key
  send_key_files
fi

echo "== the launcher"
on_box | "$here/box_ssh.sh" "$id" "bash -s -- start $(printf '%q ' "$id" "${launcher[@]}")"
