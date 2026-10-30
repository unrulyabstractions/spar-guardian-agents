#!/usr/bin/env bash
# Start a task's screen on a box that box_setup.sh set up:
#     scripts/box/box_run.sh <instance-id> <task> <model-entry> <suffix> [--samples N] [--kinds KIND ...]
#
# The model's key goes from this shell's environment to /root/.box_env (mode 600) through ssh's standard input: never
# on a command line, never printed; the box checks only its length. Then box_runner.py run starts the task's cells x
# samples at once under nohup setsid, with /root/.box_env sourced; its output goes to /root/screen.log and its pid to
# /root/screen.pid. A runner alive on the box is refused. Options after the suffix go to box_runner.py run as they
# are: the pilot is `--samples 1 --kinds solo_cheat minimal_regulated`.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/../.." && pwd)
cd "$root"
if [ $# -lt 4 ]; then
  sed -n '2,10p' "$0"
  exit 2
fi
id=$1
shift

read -r -a keys <<< "$(uv run python -c \
  'import sys; from src.models.model_registry import key_env, resolve; print(" ".join(key_env(resolve(sys.argv[1]))))' \
  "$2")"
if [ ${#keys[@]} -eq 0 ]; then
  echo "refused: the registry names no key for $2"
  exit 1
fi
name=""
for key in "${keys[@]}"; do
  if [ -n "${!key:-}" ]; then
    name=$key
    break
  fi
done
if [ -z "$name" ]; then
  echo "refused: none of ${keys[*]} is set in this shell"
  exit 1
fi

echo "== the key ($name) into /root/.box_env"
printf 'export %s=%q\n' "$name" "${!name}" \
  | "$here/box_ssh.sh" "$id" 'umask 077 && cat > /root/.box_env.new && mv /root/.box_env.new /root/.box_env'
"$here/box_ssh.sh" "$id" bash -s -- "$name" <<'REMOTE'
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

echo "== the runner"
"$here/box_ssh.sh" "$id" bash -s -- "$@" <<'REMOTE'
set -euo pipefail
start() {
  cd /root/work
  if [ -f /root/screen.pid ] && grep -qs box_runner "/proc/$(cat /root/screen.pid)/cmdline"; then
    echo "refused: a runner is alive (pid $(cat /root/screen.pid))"
    exit 1
  fi
  rm -f /root/screen.pid
  local task=$1 model=$2 suffix=$3
  shift 3
  echo "== $(date -u +%FT%TZ) box_run: $task $model $suffix $*" >> /root/screen.log
  # shellcheck disable=SC2016  # the inner bash expands $0, $1, $2 and ${@:3}
  nohup setsid bash -c '. /root/.box_env && exec .venv/bin/python -m scripts.box.box_runner run \
    --task "$0" --model "$1" --suffix "$2" "${@:3}"' "$task" "$model" "$suffix" "$@" >> /root/screen.log 2>&1 &
  local waited
  for waited in $(seq 1 60); do
    if [ -f /root/screen.pid ] && grep -qs box_runner "/proc/$(cat /root/screen.pid)/cmdline"; then
      echo "   runner pid $(cat /root/screen.pid), started after ${waited} s"
      sleep 15
      tail -n 5 /root/screen.log
      grep -qs box_runner "/proc/$(cat /root/screen.pid)/cmdline" || { echo "!! the runner ended at once"; exit 1; }
      exit 0
    fi
    sleep 1
  done
  echo "!! no runner after 60 s; the end of /root/screen.log:"
  tail -n 30 /root/screen.log
  exit 1
}
start "$@" < /dev/null
REMOTE
