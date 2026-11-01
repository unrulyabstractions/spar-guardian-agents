#!/usr/bin/env bash
# The cloud box guard's teardown adapter: .claude/box_teardown.sh <provider> <instance id>
#
# Exits 0 only after scripts/box/capture_check.sh proved that this machine holds every file the box made and the
# box is gone from the account. Both scripts gate themselves: a failed capture leaves the box alone.
#
# A stopped or hung run leaves Docker containers, and the capture refuses while they hold its workspace. This
# script then follows the capture's own procedure: it stops each container, copies its upper dir and every
# volume under /root/docker_saved, pulls them through the capture (sizes and sha256), and only then passes
# --accept-docker. A destroy that raises after its DELETE was sent counts as done once the account lacks the box.
# Any other provider is refused.
set -uo pipefail
provider=${1:-}
id=${2:-}
root=$(cd "$(dirname "$0")/.." && pwd)
cd "$root" || exit 1
if [ "$provider" != vast ]; then
  echo "!! box_teardown.sh knows only vast.ai boxes of this repository, not $provider $id"
  exit 1
fi
out=$(mktemp)
trap 'rm -f "$out"' EXIT
echo "== $(date -u +%FT%TZ) capture $id"
scripts/box/capture_check.sh "$id" --pull > "$out" 2>&1
rc=$?
cat "$out"
if [ $rc -ne 0 ]; then
  if ! grep -q "Docker holds" "$out"; then
    echo "!! the capture check failed: the box stays"
    exit 1
  fi
  echo "== $(date -u +%FT%TZ) Docker holds work: saving every container and volume under /root/docker_saved"
  scripts/box/box_ssh.sh "$id" 'bash -s' <<'REMOTE' || { echo "!! saving the containers failed: the box stays"; exit 1; }
set -u
mkdir -p /root/docker_saved/containers /root/docker_saved/volumes
for c in $(docker ps -aq); do
  name=$(docker inspect -f '{{.Name}}' "$c" | tr -d /)
  docker stop -t 20 "$c" > /dev/null 2>&1 || true
  upper=$(docker inspect -f '{{.GraphDriver.Data.UpperDir}}' "$c" 2>/dev/null)
  if [ -n "$upper" ] && [ -d "$upper" ]; then
    mkdir -p "/root/docker_saved/containers/$name" && cp -a "$upper/." "/root/docker_saved/containers/$name/" || exit 1
  fi
done
for v in $(docker volume ls -q); do
  mount=$(docker volume inspect -f '{{.Mountpoint}}' "$v")
  if [ -d "$mount" ]; then
    mkdir -p "/root/docker_saved/volumes/$v" && cp -a "$mount/." "/root/docker_saved/volumes/$v/" || exit 1
  fi
done
echo "saved: $(find /root/docker_saved -type f | wc -l) files"
REMOTE
  scripts/box/capture_check.sh "$id" --pull --accept-docker \
    "box_teardown.sh stopped every container and copied its upper dir and every volume to /root/docker_saved, which this capture pulled and checked" \
    || { echo "!! the capture check failed after the save: the box stays"; exit 1; }
fi
echo "== $(date -u +%FT%TZ) destroy $id"
if uv run python -m scripts.box.fleet destroy "$id"; then
  exit 0
fi
echo "!! the destroy raised; checking whether the account still holds $id"
for _ in 1 2 3 4 5 6; do
  sleep 20
  if uv run python - "$id" <<'EOF'
import sys
from scripts.box.vast_api import Vast, instance
sys.exit(0 if instance(Vast(writes=False), int(sys.argv[1])) is None else 1)
EOF
  then
    echo "== $id is gone from the account: the destroy took"
    uv run python -m scripts.box.fleet status > /dev/null 2>&1
    exit 0
  fi
done
echo "!! $id is still on the account"
exit 1
