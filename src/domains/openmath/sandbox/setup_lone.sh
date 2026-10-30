#!/usr/bin/env bash
set -euo pipefail
cd {workdir}
tar -xpzf {workdir}/.stage.tgz -C {workdir}
rm -f {workdir}/.stage.tgz
chown {agent_name}:{agent_name} {workdir}
