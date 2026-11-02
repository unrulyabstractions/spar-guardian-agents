#!/usr/bin/env bash
# The cloud box guard's teardown adapter: .claude/box_teardown.sh <provider> <instance id>
#
# Exits 0 only after scripts/box/capture_check.sh proved that this machine holds every file the box made and the
# box is gone from the account. Both scripts gate themselves: a failed capture leaves the box alone.
#
# A stopped or hung run leaves Docker containers, and the capture refuses while they hold its workspace. This
# script then follows the capture's own procedure: it stops each container, copies its upper dir and every
# volume under /root/docker_saved, pulls them through the capture (sizes and sha256), and only then passes
# --accept-docker; Go's build cache inside a container is left out and its size recorded (a package cache rebuilt
# from source). It never stops a container when the same capture reports a process still producing, or a box
# that names another id in /root/box_id, and the box itself must name this id before any docker stop: a save on
# the wrong box, or over runs that still write, kills those runs. A run killed as hung leaves its sandboxes
# running, so running containers alone are no reason to refuse. A destroy that raises after its DELETE was sent
# counts as done once the account lacks the box. Any other provider is refused.
set -uo pipefail
provider=${1:-}
id=${2:-}
root=$(cd "$(dirname "$0")/.." && pwd)
cd "$root" || exit 1
if [ "$provider" != vast ]; then
  echo "!! box_teardown.sh knows only vast.ai boxes of this repository, not $provider $id"
  exit 1
fi
case $id in
  ''|*[!0-9]*) echo "!! not a vast.ai instance id: $id"; exit 1 ;;   # it also goes into the box's command line
esac
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
  if grep -q -e "still producing" -e "names itself" "$out"; then
    echo "!! the capture reports a process still producing, or a box that names another id: no container is" \
         "stopped, and the box stays"
    exit 1
  fi
  echo "== $(date -u +%FT%TZ) Docker holds work: saving every container and volume under /root/docker_saved"
  scripts/box/box_ssh.sh "$id" bash -s -- "$id" <<'REMOTE' || { echo "!! saving the containers failed: the box stays"; exit 1; }
set -u
want=${1:-}
got=$(cat /root/box_id 2>/dev/null || true)
if [ -z "$want" ] || [ "$got" != "$want" ]; then
  echo "refused: /root/box_id names ${got:-nothing}, not ${want:-an id}: the endpoint reaches another box, no container is stopped"
  exit 3
fi
mkdir -p /root/docker_saved/containers /root/docker_saved/volumes
# Go's build cache (root/.cache/go-build, about 1 GB per agent container) is rebuilt from source: a package cache,
# left out of the save so a slow uplink does not keep the box billing; each size goes into EXCLUDED_go_build.txt
echo "Go build caches left out of the save on $(date -u +%FT%TZ): Go rebuilds them from source." \
  > /root/docker_saved/EXCLUDED_go_build.txt
for c in $(docker ps -aq); do
  name=$(docker inspect -f '{{.Name}}' "$c" | tr -d /)
  docker stop -t 20 "$c" > /dev/null 2>&1 || true
  upper=$(docker inspect -f '{{.GraphDriver.Data.UpperDir}}' "$c" 2>/dev/null)
  if [ -n "$upper" ] && [ -d "$upper" ]; then
    if [ -d "$upper/root/.cache/go-build" ]; then
      echo "$(du -sk "$upper/root/.cache/go-build" | cut -f1)	$name/root/.cache/go-build" \
        >> /root/docker_saved/EXCLUDED_go_build.txt
    fi
    mkdir -p "/root/docker_saved/containers/$name" || exit 1
    tar -C "$upper" --exclude=./root/.cache/go-build -cf - . | tar -C "/root/docker_saved/containers/$name" -xf - \
      || exit 1
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
