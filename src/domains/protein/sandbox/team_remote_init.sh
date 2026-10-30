# Run as root in one agent's machine: the bare team remote on the volume every machine mounts, with no commit. It
# refuses non-fast-forward pushes (git init --shared=group), and the group of `user`, the same in every machine,
# writes it. The lock keeps an agent from planting a hook or a config that runs in a teammate's machine on a push, or
# as root when the remote is read.
set -euo pipefail
git init -q --bare --shared=group --initial-branch={branch} {remote}
{lock}