# Run as root in one agent's machine: the bare team remote on the volume every machine mounts, with no commit. It
# refuses non-fast-forward pushes (git init --shared=group), and the group of `user`, the same in every machine,
# writes it. Its hooks and config stay root's, and the sticky bit keeps an agent from renaming or deleting them, so no
# agent can plant a hook that runs in a teammate's machine when the teammate pushes.
set -euo pipefail
git init -q --bare --shared=group --initial-branch={branch} {remote}
chgrp -R {group} {remote}
chmod -R g+rwX {remote}
find {remote} -type d -exec chmod g+s {{}} +
chmod -R g-w {remote}/hooks {remote}/config
chmod +t {remote}
