# Run as `user` in one agent's machine: the agent's repository on {branch}, with the team remote as its origin, and the
# agent's name as its git identity.
set -euo pipefail
git init --quiet --initial-branch={branch} {work}
git -C {work} remote add origin {remote}
git config --global user.name {name}
git config --global user.email {name}@team.internal
