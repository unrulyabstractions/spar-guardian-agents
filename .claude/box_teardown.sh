#!/usr/bin/env bash
# The cloud box guard's teardown adapter: .claude/box_teardown.sh <provider> <instance id>
#
# Exits 0 only after scripts/box/capture_check.sh proved that this machine holds every file the box made and
# scripts/box/fleet destroy removed the box. Both scripts gate themselves: a failed capture leaves the box
# alone. Any other provider is refused.
set -uo pipefail
provider=${1:-}
id=${2:-}
root=$(cd "$(dirname "$0")/.." && pwd)
cd "$root" || exit 1
if [ "$provider" != vast ]; then
  echo "!! box_teardown.sh knows only vast.ai boxes of this repository, not $provider $id"
  exit 1
fi
echo "== $(date -u +%FT%TZ) capture $id"
if ! scripts/box/capture_check.sh "$id" --pull; then
  echo "!! the capture check failed: the box stays"
  exit 1
fi
echo "== $(date -u +%FT%TZ) destroy $id"
uv run python -m scripts.box.fleet destroy "$id"
