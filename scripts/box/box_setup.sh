#!/usr/bin/env bash
# Set up a rented box for its task: scripts/box/box_setup.sh <instance-id> [<task>]
#
# Runs on the Mac right after `fleet.py create`. The task and what to deploy come from the registry and the box's launch
# (fleet.py entry); a task given here must be the registry's. In order:
#  0. the box's ssh, waited for up to 30 minutes with `fleet.py status` refreshing the endpoint each pass. A VM boots
#     behind its port: another sshd answers first and its host key would lock the VM out, so until the baseline exists
#     each try starts from an empty known_hosts. After the baseline a changed key stays refused;
#  1. the box's id into /root/box_id at first contact (a later run requires the same id), then the baseline of the
#     box's home and /tmp before anything else changes (box_capture.py baseline; a box past first contact gets none,
#     and its capture then requires the host's files too);
#  2. the commit the launch pinned, which origin/main must hold, and the submodule commits it records; this
#     checkout's HEAD and local edits are listed, since the box gets the pinned commit alone;
#  3. what only the Mac has goes up through ssh, both small: the lucy submodule's pinned commit as a git bundle (that
#     commit's history and no other branch, at most 20 MB) and the package list the launch pinned;
#  4. and 5. box_setup_remote.sh, sent to the box and started there under nohup setsid: the box's cores, RAM and disk,
#     its packages, the clone, the .venv and the task's rows and images (box_runner.py build). This script prints
#     /root/setup.log as it grows and exits with the setup's exit code. A drop of this machine's network costs nothing:
#     the setup goes on, and running this again follows a setup alive on the box, or resumes one that ended.
# The box clones what is public itself, since the Mac's uplink is slow. No GitHub token and no model key goes to the
# box here; box_run.sh sends the model's key.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/../.." && pwd)
cd "$root"
if [ $# -lt 1 ] || [ $# -gt 2 ]; then
  sed -n '2,22p' "$0"
  exit 2
fi
id=$1
setup_limit_s=7200

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

echo "== 0. the box's ssh"
known=logs/boxes/$id/known_hosts
waited=0
while true; do
  account=$(uv run python -m scripts.box.fleet status 2>&1 || true)
  case $account in
    *"!! $id "*"cannot start"*)
      echo "refused: $(grep "^!! $id " <<< "$account")"
      exit 1 ;;
  esac
  [ -f "logs/boxes/$id/baseline.jsonl" ] || rm -f "$known"
  if [ -f "logs/boxes/$id/ssh_config" ] && "$here/box_ssh.sh" "$id" true 2> /dev/null; then
    break
  fi
  if [ "$waited" -ge 1800 ]; then
    echo "refused: no ssh into $id after $waited s of waiting; it bills while it stays: look at fleet.py status"
    exit 1
  fi
  sleep 20
  waited=$((waited + 20))
done
echo "   answered after about $waited s"

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
"$here/box_scp.sh" "$id" "$here/box_setup_remote.sh" box:/root/ship/
# ssh joins its arguments into one command line for the box's shell, so each one is quoted for that shell first.
# Detached from this ssh, so a drop of this machine's network never kills a build; a setup alive on the box is
# followed, never started twice.
start=$("$here/box_ssh.sh" "$id" "if grep -qs box_setup_remote /proc/\$(cat /root/setup.pid 2> /dev/null)/cmdline; then \
cat /root/setup.log | wc -l; else rm -f /root/setup.exit; echo \$(cat /root/setup.log 2> /dev/null | wc -l) started; \
nohup setsid bash /root/ship/box_setup_remote.sh $(printf '%q ' "$commit" "$cheatbench" "$orbit" "$lucy" \
  "$uv_version" "$python_version" "$task" "$url" "$cores" "$ram_gb" "$min_cores" "$min_ram_gb" "$min_disk_gb") \
> /dev/null 2>&1 < /dev/null & fi")
seen=${start%% *}
case $start in
  *started*) echo "   started" ;;
  *) echo "   a setup is alive on the box; following it" ;;
esac

# Each pass prints the new lines of /root/setup.log. The exit code is read first, so a pass that sees it has the whole
# log. A pass that fails is a drop of the network and costs nothing but the wait: the box goes on.
deadline=$(($(date +%s) + setup_limit_s))
misses=0
while true; do
  if page=$("$here/box_ssh.sh" "$id" "code=\$(cat /root/setup.exit 2> /dev/null); \
if grep -qs box_setup_remote /proc/\$(cat /root/setup.pid 2> /dev/null)/cmdline; then alive=yes; else alive=no; fi; \
n=\$(wc -l < /root/setup.log); sed -n \"$((seen + 1)),\$n p\" /root/setup.log; echo \"@@ \$n \$alive \$code\"" 2> /dev/null)
  then
    misses=0
    last=${page##*$'\n'}
    [ "$page" = "$last" ] || printf '%s\n' "${page%$'\n'*}"
    read -r _ seen alive code <<< "$last"
    if [ -n "${code:-}" ]; then
      echo "== the box's setup exited $code"
      exit "$code"
    fi
    if [ "$alive" = no ]; then
      sleep 5
      if [ -z "$("$here/box_ssh.sh" "$id" "cat /root/setup.exit 2> /dev/null")" ]; then
        echo "!! the box's setup is gone with no exit code: see /root/setup.log, then run this again"
        exit 1
      fi
      continue
    fi
  else
    misses=$((misses + 1))
    echo "   $(date -u +%H:%M:%S) no answer from the box ($misses in a row); the setup goes on there"
  fi
  if [ "$(date +%s)" -gt "$deadline" ]; then
    echo "!! the box's setup still runs after $setup_limit_s s; it goes on there: run this again to follow it"
    exit 1
  fi
  sleep 15
done
