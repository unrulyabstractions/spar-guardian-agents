#!/usr/bin/env bash
# Start a box's screen: scripts/box/box_run.sh <instance-id> [--suffix S] [--samples N] [--kinds KIND ...]
#                                                                [--allow-main-key] [--mock]
#
# The task, the model and the commit come from the registry through fleet.py run-args, which also takes the run names'
# suffix (the model's, or a --suffix that starts with it) and refuses a run name this machine holds from a run made
# elsewhere. The model's key goes from this shell's environment to /root/.box_env (mode 600) through ssh's standard
# input: never on a command line, never printed; the box checks only its length. The key sent is BOX_<NAME>, a key
# made for the screen with a spend cap and revoked after the destroys, when this shell sets it; <NAME> itself only
# with --allow-main-key. Then, once /root/box_id shows the endpoint reaches this instance, box_runner.py run starts the
# task's cells x samples at once under nohup setsid, with /root/.box_env sourced; its output goes to /root/screen.log
# and its pid to /root/screen.pid. A runner alive on the box is refused. The pilot is
# `--samples 1 --kinds solo_cheat minimal_regulated`. --mock rehearses the box on Inspect's mock model: no key goes to
# the box and nothing is paid, and the run names end in <suffix>mock, so they never stand in for the screen's runs.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/../.." && pwd)
cd "$root"
if [ $# -lt 1 ]; then
  sed -n '2,15p' "$0"
  exit 2
fi
id=$1
shift
allow_main=0
mock=0
pass=()
for arg in "$@"; do
  case $arg in
    --allow-main-key) allow_main=1 ;;
    --mock) mock=1; pass+=("$arg") ;;
    *) pass+=("$arg") ;;
  esac
done

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
uv run python -m scripts.box.fleet run-args "$id" ${pass[@]+"${pass[@]}"} > "$work/args"
runner=()
while IFS= read -r line; do
  runner+=("$line")
done < "$work/args"
model=$(uv run python -m scripts.box.fleet entry "$id" model)
echo "== box $id: box_runner.py run ${runner[*]}"

# send_key: the model's key into /root/.box_env, its length checked on the box; never printed.
send_key() {
  read -r -a keys <<< "$(uv run python -c \
    'import sys; from src.models.model_registry import key_env, resolve; print(" ".join(key_env(resolve(sys.argv[1]))))' \
    "$model")"
  if [ ${#keys[@]} -eq 0 ]; then
    echo "refused: the registry names no key for $model"
    exit 1
  fi
  name=""
  sent_from=""
  for key in "${keys[@]}"; do
    scoped=BOX_$key
    if [ -n "${!scoped:-}" ]; then
      name=$key
      sent_from=$scoped
      break
    fi
  done
  if [ -z "$name" ] && [ "$allow_main" -eq 1 ]; then
    for key in "${keys[@]}"; do
      if [ -n "${!key:-}" ]; then
        name=$key
        sent_from=$key
        break
      fi
    done
  fi
  if [ -z "$name" ]; then
    echo "refused: set BOX_${keys[0]} to a key made for this screen with a spend cap (an OpenRouter key limit, an" \
      "OpenAI project budget, an Anthropic workspace limit), or pass --allow-main-key to send ${keys[*]} itself to a" \
      "third-party host"
    exit 1
  fi

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

if [ "$mock" -eq 1 ]; then
  echo "== the mock model: no key goes to the box"
else
  send_key
fi

echo "== the runner"
"$here/box_ssh.sh" "$id" "bash -s -- $(printf '%q ' "$id" "${runner[@]}")" <<'REMOTE'
set -euo pipefail
start() {
  local id=$1
  shift
  if [ "$(cat /root/box_id 2> /dev/null || true)" != "$id" ]; then
    echo "refused: /root/box_id names $(cat /root/box_id 2> /dev/null || echo nothing), not $id: the endpoint" \
      "reaches another box, or box_setup.sh never ran"
    exit 1
  fi
  cd /root/work
  if [ -f /root/screen.pid ] && grep -qs box_runner "/proc/$(cat /root/screen.pid)/cmdline"; then
    echo "refused: a runner is alive (pid $(cat /root/screen.pid))"
    exit 1
  fi
  rm -f /root/screen.pid
  echo "== $(date -u +%FT%TZ) box_run: $*" >> /root/screen.log
  # shellcheck disable=SC2016  # the inner bash expands "$@"
  # The .venv's bin on PATH, as uv run gives it: creative writing's scorer compiles with the python3.11 it finds there.
  nohup setsid bash -c 'export PATH=/root/work/.venv/bin:$PATH; [ ! -f /root/.box_env ] || . /root/.box_env; \
exec .venv/bin/python -m scripts.box.box_runner run "$@"' runner "$@" >> /root/screen.log 2>&1 &
  local launched=$! waited
  for waited in $(seq 1 60); do
    if [ -f /root/screen.pid ] && grep -qs box_runner "/proc/$(cat /root/screen.pid)/cmdline"; then
      echo "   runner pid $(cat /root/screen.pid), started after ${waited} s"
      sleep 15
      tail -n 5 /root/screen.log
      grep -qs box_runner "/proc/$(cat /root/screen.pid)/cmdline" || { echo "!! the runner ended at once"; exit 1; }
      exit 0
    fi
    kill -0 "$launched" 2> /dev/null || break
    sleep 1
  done
  echo "!! no runner; the end of /root/screen.log:"
  tail -n 30 /root/screen.log
  exit 1
}
start "$@" < /dev/null
REMOTE
