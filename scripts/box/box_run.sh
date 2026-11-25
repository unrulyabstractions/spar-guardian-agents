#!/usr/bin/env bash
# Start a box's launcher: scripts/box/box_run.sh <instance-id> [--mock] [--main-key]
#
# The launcher is the Mac's own, scripts/study/run_study.py, on the box's plan: `run_study <plan> --yes [--gates
# <spec>] --only <the box's targets> --limit max_agents=<its agents at once>`, all from the box's registry entry through
# fleet.py run-args, which refuses a model that needs a key a box does not hold and a run of the box this machine holds
# from a run made elsewhere. --mock passes --mock: every model is Inspect's mock model, the runs go to <study>_mock, no
# key goes to the box and nothing is paid. Running this again on a box whose launcher ended starts the runs not stored.
#
# A box holds one key, OPENROUTER_API_KEY. It goes from this shell's environment to /root/.box_env (mode 600) through
# ssh's standard input: never on a command line, never printed. The box checks only its length. The key sent is
# BOX_OPENROUTER_API_KEY alone: a key made for this launch with a credit limit in OpenRouter's dashboard, revoked after
# the destroys. scripts/box/box_key.py refuses it first unless OpenRouter caps it and enough is left for every run the
# box may make at its dollar cap. --main-key sends OPENROUTER_API_KEY instead, by the user's word for a study, under
# those same two checks.
#
# Once /root/box_id shows the endpoint reaches this instance, the launcher starts under nohup setsid with /root/.box_env
# sourced, uv on its PATH and UV_NO_SYNC set (the cells' `uv run` uses the .venv the launch pinned as it is). Its output
# goes to /root/launcher.log and its pid to /root/launcher.pid. A launcher alive on the box is refused.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/../.." && pwd)
cd "$root"
if [ $# -lt 1 ]; then
  sed -n '2,19p' "$0"
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
echo "== box $id: python -m ${launcher[*]}"

# send_key: the box's key into /root/.box_env, its length checked on the box; never printed.
send_key() {
  sent_from=BOX_$name
  check=(check "$name" --box-id "$id")
  if [ "$main_key" -eq 1 ]; then
    sent_from=$name
    check+=(--main-key)
  fi
  # OpenRouter must cap the key and enough must be left for the box's runs; only --main-key lets the main key pass
  uv run python -m scripts.box.box_key "${check[@]}" || exit 1

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

echo "== the launcher"
"$here/box_ssh.sh" "$id" "bash -s -- $(printf '%q ' "$id" "${launcher[@]}")" <<'REMOTE'
set -euo pipefail
alive() { [ -f /root/launcher.pid ] && grep -qs scripts.study.run_study "/proc/$(cat /root/launcher.pid)/cmdline"; }
start() {
  local id=$1
  shift
  if [ "$(cat /root/box_id 2> /dev/null || true)" != "$id" ]; then
    echo "refused: /root/box_id names $(cat /root/box_id 2> /dev/null || echo nothing), not $id: the endpoint" \
      "reaches another box, or box_setup.sh never ran"
    exit 1
  fi
  cd /root/work
  if alive; then
    echo "refused: a launcher is alive (pid $(cat /root/launcher.pid))"
    exit 1
  fi
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
start "$@" < /dev/null
REMOTE
