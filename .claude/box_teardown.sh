#!/usr/bin/env bash
# The cloud box guard's teardown adapter: .claude/box_teardown.sh <provider> <instance id>
#
# Exits 0 only after scripts/box/capture_check.sh proved that this machine holds every file the box made and the
# box is gone from the account. Both scripts gate themselves: a failed capture leaves the box alone.
#
# A stopped or hung run leaves Docker containers, and the capture refuses while they hold its workspace (its problem
# `Docker holds N running and M containers in all`). This script then follows the capture's own procedure: it stops
# each container, copies its upper dir and every volume under /root/docker_saved, pulls them through the capture
# (sizes and sha256), and only then passes --accept-docker; Go's build cache inside a container is left out and its
# size recorded (a package cache rebuilt from source). The save fails, and the box stays, when Docker cannot list what
# it holds, when a container's upper dir cannot be copied, or when it saved nothing.
#
# When the capture cannot tell what Docker holds (its daemon is dead or hung), this script restarts Docker once on the
# box and runs the capture again, which then sees the containers and goes on as above. When Docker still cannot
# answer, it stops the box through fleet.py stop (the disk stays, the bill drops to storage) and says so: a person
# must capture what Docker holds.
#
# It never stops a container or restarts Docker when the same capture reports a process still producing, or a box
# that names another id in /root/box_id, and the box itself must name this id before either: a save on the wrong box,
# or over runs that still write, kills those runs. A run killed as hung leaves its sandboxes running, so running
# containers alone are no reason to refuse. A destroy that raises after its DELETE was sent counts as done once the
# account lacks the box. Any other provider is refused.
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
docker_holds='^!! Docker holds [0-9]+ running and [0-9]+ containers in all'
docker_unknown='^!! cannot tell what Docker holds'

# capture ARGS...: the capture check with --pull, its output kept in $out and printed; its exit code.
capture() {
  echo "== $(date -u +%FT%TZ) capture $id $*"
  scripts/box/capture_check.sh "$id" --pull "$@" > "$out" 2>&1
  local rc=$?
  cat "$out"
  return $rc
}

# may_touch_docker: whether the last capture lets this script act on Docker: no process still producing, and the box
# names this id.
may_touch_docker() {
  if grep -q -e "still producing" -e "names itself" "$out"; then
    echo "!! the capture reports a process still producing, or a box that names another id: Docker is left as it" \
         "is, and the box stays"
    return 1
  fi
}

capture
rc=$?
if [ $rc -ne 0 ] && grep -Eq "$docker_unknown" "$out"; then
  may_touch_docker || exit 1
  echo "== $(date -u +%FT%TZ) Docker cannot answer: restarting it once on the box"
  scripts/box/box_ssh.sh "$id" bash -s -- "$id" <<'REMOTE'
set -u
want=${1:-}
got=$(cat /root/box_id 2>/dev/null || true)
if [ -z "$want" ] || [ "$got" != "$want" ]; then
  echo "refused: /root/box_id names ${got:-nothing}, not ${want:-an id}: the endpoint reaches another box"
  exit 3
fi
systemctl restart docker || echo "!! systemctl restart docker failed"
for _ in $(seq 1 30); do
  if docker info > /dev/null 2>&1; then
    echo "   Docker answers after its restart"
    exit 0
  fi
  sleep 2
done
echo "!! Docker does not answer 60 s after its restart"
REMOTE
  restarted=$?
  if [ $restarted -eq 3 ]; then
    echo "!! the endpoint reaches another box: the box stays"
    exit 1
  fi
  capture
  rc=$?
  if [ $rc -ne 0 ] && grep -Eq "$docker_unknown" "$out"; then
    echo "!! Docker still cannot tell what it holds after a restart: its containers may hold a run's workspace, so" \
         "the box is stopped, not destroyed. A person must capture what Docker holds."
    uv run python -m scripts.box.fleet stop "$id"
    exit 1
  fi
fi
if [ $rc -ne 0 ]; then
  if ! grep -Eq "$docker_holds" "$out"; then
    echo "!! the capture check failed: the box stays"
    exit 1
  fi
  may_touch_docker || exit 1
  echo "== $(date -u +%FT%TZ) Docker holds work: saving every container and volume under /root/docker_saved"
  if ! scripts/box/box_ssh.sh "$id" bash -s -- "$id" <<'REMOTE'
set -u
want=${1:-}
got=$(cat /root/box_id 2>/dev/null || true)
if [ -z "$want" ] || [ "$got" != "$want" ]; then
  echo "refused: /root/box_id names ${got:-nothing}, not ${want:-an id}: the endpoint reaches another box, no" \
    "container is stopped"
  exit 3
fi
containers=$(docker ps -aq) || { echo "!! Docker cannot list its containers: nothing saved"; exit 1; }
volumes=$(docker volume ls -q) || { echo "!! Docker cannot list its volumes: nothing saved"; exit 1; }
mkdir -p /root/docker_saved/containers /root/docker_saved/volumes
# Go's build cache (root/.cache/go-build, about 1 GB per agent container) is rebuilt from source: a package cache,
# left out of the save so a slow uplink does not keep the box billing; each size goes into EXCLUDED_go_build.txt
echo "Go build caches left out of the save on $(date -u +%FT%TZ): Go rebuilds them from source." \
  > /root/docker_saved/EXCLUDED_go_build.txt
saved_c=0
saved_v=0
for c in $containers; do
  name=$(docker inspect -f '{{.Name}}' "$c" | tr -d /)
  docker stop -t 20 "$c" > /dev/null 2>&1 || true
  upper=$(docker inspect -f '{{.GraphDriver.Data.UpperDir}}' "$c" 2>/dev/null)
  if [ -z "$name" ] || [ -z "$upper" ] || [ ! -d "$upper" ]; then
    echo "!! container $c ${name:-unnamed}: no upper dir to copy"
    exit 1
  fi
  if [ -d "$upper/root/.cache/go-build" ]; then
    echo "$(du -sk "$upper/root/.cache/go-build" | cut -f1)	$name/root/.cache/go-build" \
      >> /root/docker_saved/EXCLUDED_go_build.txt
  fi
  mkdir -p "/root/docker_saved/containers/$name" || exit 1
  tar -C "$upper" --exclude=./root/.cache/go-build -cf - . | tar -C "/root/docker_saved/containers/$name" -xf - \
    || exit 1
  saved_c=$((saved_c + 1))
done
for v in $volumes; do
  mount=$(docker volume inspect -f '{{.Mountpoint}}' "$v")
  if [ -z "$mount" ] || [ ! -d "$mount" ]; then
    echo "!! volume $v: no mount point to copy"
    exit 1
  fi
  mkdir -p "/root/docker_saved/volumes/$v" && cp -a "$mount/." "/root/docker_saved/volumes/$v/" || exit 1
  saved_v=$((saved_v + 1))
done
if [ $((saved_c + saved_v)) -eq 0 ]; then
  echo "!! Docker lists no container and no volume: nothing saved"
  exit 1
fi
echo "saved: $saved_c containers, $saved_v volumes, $(find /root/docker_saved -type f | wc -l | tr -d " ") files"
REMOTE
  then
    echo "!! saving the containers failed: the box stays"
    exit 1
  fi
  reason="box_teardown.sh stopped every container and copied its upper dir and every volume to /root/docker_saved,"
  reason+=" which this capture pulled and checked"
  capture --accept-docker "$reason" || { echo "!! the capture check failed after the save: the box stays"; exit 1; }
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
