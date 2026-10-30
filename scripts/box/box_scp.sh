#!/usr/bin/env bash
# Copy files to or from a registered box: scripts/box/box_scp.sh <instance-id> <source> ... <target>
#
# A path on the box is written box:<path>, as in `scripts/box/box_scp.sh 58129905 box:/root/screen.log logs/`. The
# connection is logs/boxes/<id>/ssh_config, as for box_ssh.sh, and each argument reaches scp as one word.
set -euo pipefail
root=$(cd "$(dirname "$0")/../.." && pwd)
if [ $# -lt 3 ]; then
  sed -n '2,5p' "$0"
  exit 2
fi
config="$root/logs/boxes/$1/ssh_config"
if [ ! -f "$config" ]; then
  echo "no $config: run uv run python -m scripts.box.fleet status" >&2
  exit 2
fi
shift
exec scp -F "$config" "$@"
