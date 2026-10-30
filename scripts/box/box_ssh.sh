#!/usr/bin/env bash
# ssh to a registered box: scripts/box/box_ssh.sh <instance-id> [command ...]
#
# The connection comes from logs/boxes/<id>/ssh_config, which `uv run python -m scripts.box.fleet status` writes: the
# box's direct endpoint, root, ~/.ssh/id_ed25519 alone, the box's own known_hosts, no prompts. Each argument after the
# id reaches ssh as one word, so the wrapper behaves the same when zsh or bash calls it. ssh itself joins those words
# with spaces into one command line for the box's shell, as ssh always does: a caller quotes each argument for that
# shell (printf %q) when it may hold a space or a shell character.
set -euo pipefail
root=$(cd "$(dirname "$0")/../.." && pwd)
if [ $# -lt 1 ]; then
  sed -n '2,8p' "$0"
  exit 2
fi
config="$root/logs/boxes/$1/ssh_config"
if [ ! -f "$config" ]; then
  echo "no $config: run uv run python -m scripts.box.fleet status" >&2
  exit 2
fi
shift
exec ssh -F "$config" box "$@"
