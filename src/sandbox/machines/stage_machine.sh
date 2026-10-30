#!/usr/bin/env bash
# Extract the staging archive at / as root: the archive's owners (uid 1000), modes and mtimes apply, and the
# directories it carries get their mtimes once their files are in place.
set -euo pipefail
tar -xpzf /root/.stage.tgz --delay-directory-restore -C /
rm -f /root/.stage.tgz
