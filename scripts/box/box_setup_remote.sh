#!/usr/bin/env bash
# On the box, detached from ssh: box_setup.sh sends this file to /root/ship/ and starts it under nohup setsid, so a drop
# of the Mac's network never kills a build. Its output goes to /root/setup.log, its pid to /root/setup.pid and its exit
# code, when it ends, to /root/setup.exit, which box_setup.sh polls. In order: the box's cores, RAM and disk held to the
# fleet's minimums; docker-compose-v2, git and rsync from apt, uv at the launch's version; this repository cloned from
# GitHub at the commit with cheatbench and orbit from GitHub and lucy from the bundle; the .venv at the launch's Python
# from the package list without dependency resolution (ORBIT editable from its submodule), held to the list package by
# package, and an import check; the task's rows and images (box_runner.py build).
set -euo pipefail
exec >> /root/setup.log 2>&1 < /dev/null
echo $$ > /root/setup.pid
trap 'echo $? > /root/setup.exit' EXIT
setup() {
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
  # Ubuntu's automatic upgrades stop here: they would change the host under running sandboxes, and one that reached
  # install-info left dpkg half configured, since vast.ai's /etc/environment does not parse as a shell file. apt-get
  # then fails for good, so a box that holds the tools skips it.
  systemctl disable --now unattended-upgrades.service apt-daily.timer apt-daily-upgrade.timer || true
  export DEBIAN_FRONTEND=noninteractive
  if ! { docker compose version && git --version && rsync --version && curl --version && python3 --version; } \
      > /dev/null 2>&1; then
    apt-get -o DPkg::Lock::Timeout=900 update -qq
    apt-get -o DPkg::Lock::Timeout=900 install -y -qq docker-compose-v2 git rsync curl ca-certificates python3
  fi
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
