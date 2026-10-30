#!/usr/bin/env bash
# Set up a rented box for one task: scripts/box/box_setup.sh <instance-id> <task>
#
# Runs on the Mac once `fleet.py status` has written the box's ssh config. In order:
#  1. the baseline of the box's home and /tmp before anything changes (box_capture.py baseline), for the capture;
#  2. the commit to deploy: this checkout's HEAD, which origin/main must hold, and the submodule commits it records;
#     local edits outside that commit are listed, since the box will not have them;
#  3. what only the Mac has goes up through ssh, both small: the lucy submodule as a git bundle and the .venv's
#     package list;
#  4. on the box: docker-compose-v2, git and rsync from apt, uv at the Mac's version, this repository cloned from
#     GitHub at the commit with cheatbench and orbit from GitHub and lucy from the bundle, the .venv at the Mac's
#     Python from the package list without dependency resolution (ORBIT editable from its submodule), held to the
#     Mac's list package by package, and an import check;
#  5. on the box: the task's rows and images (box_runner.py build).
# The box clones what is public itself, since the Mac's uplink is slow. No GitHub token and no model key goes to the
# box here; box_run.sh sends the model's key. The box keeps its own log in /root/setup.log. Running it again resumes.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/../.." && pwd)
cd "$root"
if [ $# -ne 2 ]; then
  sed -n '2,18p' "$0"
  exit 2
fi
id=$1
task=$2

uv run python -c 'import sys; from scripts.box.box_runner import BUILDS; sys.exit(sys.argv[1] not in BUILDS)' "$task" \
  || { echo "refused: no build steps for $task in scripts/box/box_runner.py BUILDS"; exit 1; }
url=$(git remote get-url origin)
case $url in
  https://*) ;;
  *) echo "refused: origin is $url; the box clones over https without credentials"; exit 1 ;;
esac

echo "== 1. the host image's own files"
uv run python -m scripts.box.box_capture baseline "$id"

echo "== 2. the commit"
git fetch --quiet origin
commit=$(git rev-parse HEAD)
git merge-base --is-ancestor "$commit" origin/main \
  || { echo "refused: HEAD $commit is not on origin/main, so the box cannot clone it: push it first"; exit 1; }
cheatbench=$(git rev-parse "$commit:submodules/cheatbench")
orbit=$(git rev-parse "$commit:submodules/orbit")
lucy=$(git rev-parse "$commit:submodules/lucy")
echo "   $commit; cheatbench $cheatbench, orbit $orbit, lucy $lucy"
edits=$(
  git status --porcelain --untracked-files=no
  for sub in cheatbench orbit lucy; do
    git -C "submodules/$sub" status --porcelain --untracked-files=no | sed "s|^\(...\)|\1submodules/$sub/|"
  done
)
if [ -n "$edits" ]; then
  echo "   not on the box, local edits outside the commit:"
  while IFS= read -r line; do
    echo "   $line"
  done <<< "$edits"
fi

echo "== 3. the lucy bundle and the package list"
ship=$(mktemp -d)
trap 'rm -rf "$ship"' EXIT
git -C submodules/lucy cat-file -e "$lucy^{commit}"
git -C submodules/lucy bundle create --quiet "$ship/lucy.bundle" --all
uv pip freeze --python .venv --exclude-editable > "$ship/requirements.txt"
grep -q '^inspect-ai==' "$ship/requirements.txt"
uv_version=$(uv --version | cut -d' ' -f2)
python_version=$(.venv/bin/python -c 'import platform; print(platform.python_version())')
ls -l "$ship"
"$here/box_ssh.sh" "$id" mkdir -p /root/ship
"$here/box_scp.sh" "$id" "$ship/lucy.bundle" "$ship/requirements.txt" box:/root/ship/

echo "== 4. and 5. on the box"
# bash reads the whole function before it runs it, so no command inside can eat the script from standard input.
"$here/box_ssh.sh" "$id" bash -s -- "$commit" "$cheatbench" "$orbit" "$lucy" "$uv_version" "$python_version" \
  "$task" "$url" <<'REMOTE'
set -euo pipefail
setup() {
  exec > >(tee -a /root/setup.log) 2>&1
  local commit=$1 cheatbench=$2 orbit=$3 lucy=$4 uv_version=$5 python_version=$6 task=$7 url=$8
  local repo=/root/work
  echo "== setup $(date -u +%FT%TZ): $commit for $task"
  if [ -f /root/screen.pid ] && grep -qs box_runner "/proc/$(cat /root/screen.pid)/cmdline"; then
    echo "refused: a runner is alive (pid $(cat /root/screen.pid))"
    exit 1
  fi
  export DEBIAN_FRONTEND=noninteractive
  apt-get -o DPkg::Lock::Timeout=900 update -q
  apt-get -o DPkg::Lock::Timeout=900 install -y -q docker-compose-v2 git rsync curl ca-certificates python3
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
    echo "!! the box's packages differ from the Mac's (above)"
    exit 1
  fi
  .venv/bin/python -c 'import inspect_ai, orbit, src.cli, examples.example_arguments; print("imports ok")'

  .venv/bin/python -m scripts.box.box_runner build --task "$task"
  echo "== setup done $(date -u +%FT%TZ); disk: $(df -h /root | tail -1)"
  docker images --format '{{.Repository}}:{{.Tag}} {{.Size}}'
}
setup "$@" < /dev/null
REMOTE
