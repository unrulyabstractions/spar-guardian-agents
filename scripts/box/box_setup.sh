#!/usr/bin/env bash
# Set up a rented box for its task: scripts/box/box_setup.sh <instance-id> [<task>]
#
# Runs on the Mac once `fleet.py status` has written the box's ssh config. The task and what to deploy come from the
# registry and the box's launch (fleet.py entry); a task given here must be the registry's. In order:
#  1. the box's id into /root/box_id at first contact (a later run requires the same id), then the baseline of the
#     box's home and /tmp before anything else changes (box_capture.py baseline; a box past first contact gets none,
#     and its capture then requires the host's files too);
#  2. the commit the launch pinned, which origin/main must hold, and the submodule commits it records; this
#     checkout's HEAD and local edits are listed, since the box gets the pinned commit alone;
#  3. what only the Mac has goes up through ssh, both small: the lucy submodule's pinned commit as a git bundle (that
#     commit's history and no other branch, at most 20 MB) and the package list the launch pinned;
#  4. on the box: its cores, RAM and disk held to the fleet's minimums; docker-compose-v2, git and rsync from apt, uv
#     at the launch's version, this repository cloned from GitHub at the commit with cheatbench and orbit from GitHub
#     and lucy from the bundle, the .venv at the launch's Python from the package list without dependency resolution
#     (ORBIT editable from its submodule), held to the list package by package, and an import check;
#  5. on the box: the task's rows and images (box_runner.py build).
# The box clones what is public itself, since the Mac's uplink is slow. No GitHub token and no model key goes to the
# box here; box_run.sh sends the model's key. The box keeps its own log in /root/setup.log. Running it again resumes.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/../.." && pwd)
cd "$root"
if [ $# -lt 1 ] || [ $# -gt 2 ]; then
  sed -n '2,20p' "$0"
  exit 2
fi
id=$1

fields=$(uv run python -m scripts.box.fleet entry "$id" task commit requirements requirements_sha256 uv_version \
  python_version cores ram_gb min_cores min_ram_gb min_disk_gb)
{
  read -r task
  read -r commit
  read -r requirements
  read -r requirements_sha256
  read -r uv_version
  read -r python_version
  read -r cores
  read -r ram_gb
  read -r min_cores
  read -r min_ram_gb
  read -r min_disk_gb
} <<< "$fields"
if [ $# -eq 2 ] && [ "$2" != "$task" ]; then
  echo "refused: the registry has box $id run $task, not $2"
  exit 1
fi
uv run python -c 'import sys; from scripts.box.box_runner import BUILDS; sys.exit(sys.argv[1] not in BUILDS)' "$task" \
  || { echo "refused: no build steps for $task in scripts/box/box_runner.py BUILDS"; exit 1; }
url=$(git remote get-url origin)
case $url in
  https://*@*) echo "refused: origin's URL carries credentials; the box clones over https without them"; exit 1 ;;
  https://*) ;;
  *) echo "refused: origin is $url; the box clones over https without credentials"; exit 1 ;;
esac

echo "== 1. the box's id and the host image's own files"
qid=$(printf %q "$id")
"$here/box_ssh.sh" "$id" "if [ -f /root/box_id ]; then [ \"\$(cat /root/box_id)\" = $qid ] \
|| { echo \"refused: this box names itself \$(cat /root/box_id), not $qid\"; exit 1; }; \
else printf '%s\\n' $qid > /root/box_id; fi; echo \"   /root/box_id: \$(cat /root/box_id)\""
code=0
uv run python -m scripts.box.box_capture baseline "$id" || code=$?
if [ "$code" -eq 3 ]; then
  echo "   no baseline for $id: its capture will require the host image's files as well"
elif [ "$code" -ne 0 ]; then
  exit "$code"
fi

echo "== 2. the commit the launch pinned"
git fetch --quiet origin
git merge-base --is-ancestor "$commit" origin/main \
  || { echo "refused: the launch's commit $commit is not on origin/main, so the box cannot clone it"; exit 1; }
cheatbench=$(git rev-parse "$commit:submodules/cheatbench")
orbit=$(git rev-parse "$commit:submodules/orbit")
lucy=$(git rev-parse "$commit:submodules/lucy")
echo "   $commit; cheatbench $cheatbench, orbit $orbit, lucy $lucy"
head=$(git rev-parse HEAD)
if [ "$head" != "$commit" ]; then
  echo "   this checkout is at $head; the box gets the launch's $commit"
fi
edits=$(
  git status --porcelain --untracked-files=no
  for sub in cheatbench orbit lucy; do
    git -C "submodules/$sub" status --porcelain --untracked-files=no | sed "s|^\(...\)|\1submodules/$sub/|"
  done
)
if [ -n "$edits" ]; then
  echo "   not on the box, local edits in this checkout:"
  while IFS= read -r line; do
    echo "   $line"
  done <<< "$edits"
fi

echo "== 3. the lucy bundle and the package list"
ship=$(mktemp -d)
trap 'rm -rf "$ship"' EXIT
git -C submodules/lucy cat-file -e "$lucy^{commit}"
# A bundle names refs, never a bare commit: a scratch repository that borrows lucy's objects holds one ref at the
# pinned commit, so the bundle carries that commit's history alone and nothing is written under submodules/.
objects=$(cd submodules/lucy && cd "$(git rev-parse --git-path objects)" && pwd)
git init --quiet --bare "$ship/lucy.git"
printf '%s\n' "$objects" > "$ship/lucy.git/objects/info/alternates"
git -C "$ship/lucy.git" update-ref refs/heads/box "$lucy"
git -C "$ship/lucy.git" bundle create --quiet "$ship/lucy.bundle" refs/heads/box
rm -rf "$ship/lucy.git"
size=$(($(wc -c < "$ship/lucy.bundle")))
if [ "$size" -gt 20000000 ]; then
  echo "refused: the lucy bundle is $size bytes, more than 20 MB to send to a third-party host"
  exit 1
fi
cp "$requirements" "$ship/requirements.txt"
if [ "$(shasum -a 256 "$ship/requirements.txt" | cut -d' ' -f1)" != "$requirements_sha256" ]; then
  echo "refused: $requirements is not the package list the launch pinned"
  exit 1
fi
ls -l "$ship"
"$here/box_ssh.sh" "$id" mkdir -p /root/ship
"$here/box_scp.sh" "$id" "$ship/lucy.bundle" "$ship/requirements.txt" box:/root/ship/

echo "== 4. and 5. on the box"
# ssh joins its arguments into one command line for the box's shell, so each one is quoted for that shell first.
# bash reads the whole function before it runs it, so no command inside can eat the script from standard input.
"$here/box_ssh.sh" "$id" "bash -s -- $(printf '%q ' "$commit" "$cheatbench" "$orbit" "$lucy" "$uv_version" \
  "$python_version" "$task" "$url" "$cores" "$ram_gb" "$min_cores" "$min_ram_gb" "$min_disk_gb")" <<'REMOTE'
set -euo pipefail
setup() {
  exec > >(tee -a /root/setup.log) 2>&1
  local commit=$1 cheatbench=$2 orbit=$3 lucy=$4 uv_version=$5 python_version=$6 task=$7 url=$8 cores=$9
  local ram_gb=${10} min_cores=${11} min_ram_gb=${12} min_disk_gb=${13}
  local repo=/root/work
  echo "== setup $(date -u +%FT%TZ): $commit for $task"
  if [ -f /root/screen.pid ] && grep -qs box_runner "/proc/$(cat /root/screen.pid)/cmdline"; then
    echo "refused: a runner is alive (pid $(cat /root/screen.pid))"
    exit 1
  fi
  local ncpu mem_gb disk_gb
  ncpu=$(nproc)
  mem_gb=$(awk '/^MemTotal:/ {printf "%d", $2 * 1024 / 1e9}' /proc/meminfo)
  disk_gb=$(df -B1 --output=size / | tail -n 1 | awk '{printf "%d", $1 / 1e9}')
  echo "   $ncpu cores, $mem_gb GB of RAM, $disk_gb GB on /; the offer: ${cores:-?} cores, ${ram_gb:-?} GB (assumed)"
  if [ "$ncpu" -lt "$min_cores" ] || [ "$mem_gb" -lt "$min_ram_gb" ] || [ "$disk_gb" -lt "$min_disk_gb" ]; then
    echo "refused: the box needs $min_cores cores, $min_ram_gb GB of RAM and $min_disk_gb GB on /"
    exit 1
  fi
  export DEBIAN_FRONTEND=noninteractive
  apt-get -o DPkg::Lock::Timeout=900 update -qq
  apt-get -o DPkg::Lock::Timeout=900 install -y -qq docker-compose-v2 git rsync curl ca-certificates python3
  echo "docker $(docker info --format '{{.ServerVersion}}'), $(docker compose version)"
  export PATH=/root/.local/bin:$PATH
  if [ "$(uv --version 2>/dev/null | cut -d' ' -f2)" != "$uv_version" ]; then
    curl -LsSf "https://astral.sh/uv/$uv_version/install.sh" | env UV_NO_MODIFY_PATH=1 INSTALLER_NO_MODIFY_PATH=1 sh
  fi
  uv --version

  [ -d "$repo/.git" ] || git clone --quiet "$url" "$repo"
  git -C "$repo" fetch --quiet origin
  git -C "$repo" checkout --quiet --detach "$commit"
  git -C "$repo" submodule update --init --quiet submodules/cheatbench submodules/orbit
  [ -d "$repo/submodules/lucy/.git" ] || git clone --quiet --no-checkout /root/ship/lucy.bundle "$repo/submodules/lucy"
  git -C "$repo/submodules/lucy" fetch --quiet /root/ship/lucy.bundle '+refs/*:refs/bundle/*'
  git -C "$repo/submodules/lucy" checkout --quiet --detach "$lucy"
  local pair
  for pair in "$repo:$commit" "$repo/submodules/cheatbench:$cheatbench" "$repo/submodules/orbit:$orbit" \
              "$repo/submodules/lucy:$lucy"; do
    if [ "$(git -C "${pair%%:*}" rev-parse HEAD)" != "${pair##*:}" ]; then
      echo "!! ${pair%%:*} is not at ${pair##*:}"
      exit 1
    fi
  done
  git -C "$repo" submodule status

  cd "$repo"
  [ -x .venv/bin/python ] || uv venv --quiet --python "$python_version" .venv
  uv pip install --quiet --python .venv/bin/python --no-deps -r /root/ship/requirements.txt
  uv pip install --quiet --python .venv/bin/python --no-deps -e submodules/orbit
  if ! diff <(uv pip freeze --python .venv/bin/python --exclude-editable) /root/ship/requirements.txt; then
    echo "!! the box's packages differ from the launch's list (above)"
    exit 1
  fi
  .venv/bin/python -c 'import inspect_ai, orbit, src.cli, examples.example_arguments; print("imports ok")'

  .venv/bin/python -m scripts.box.box_runner build --task "$task"
  echo "== setup done $(date -u +%FT%TZ); disk: $(df -h /root | tail -1)"
  docker images --format '{{.Repository}}:{{.Tag}} {{.Size}}'
}
setup "$@" < /dev/null
REMOTE
