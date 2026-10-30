#!/usr/bin/env bash
# Prove that every file a box made has a local copy before the box is destroyed:
#     scripts/box/capture_check.sh <instance-id> [--pull] [--accept-docker REASON]
#
# Sweeps every regular file under the box's clone, /root and /tmp (scripts/box/box_sweep.py, run on the box) and
# compares each one's size and sha256 with the local copy: the clone's runs/, logs/ and out/ at the same path in this
# repository (or under logs/boxes/<id>/conflicts/ when this machine's copy is newer), every other file under
# logs/boxes/<id>/files/. What is provably reproducible is counted, not required, and named by reason. Exit 0 only when
# nothing is missing or different, nothing could not be read, nothing still produces, no link leaves the swept roots,
# /root/box_id names this instance, Docker's state is known and holds no container or volume (or --accept-docker says
# why they may go), and the sweep found files; only then are logs/boxes/<id>/manifest.jsonl and capture.json written,
# which fleet.py destroy requires from the last 10 minutes. --pull fetches what is missing or different first, then
# sweeps again. scripts/box/box_capture.py holds the rules.
set -euo pipefail
cd "$(dirname "$0")/../.."
if [ $# -lt 1 ]; then
  sed -n '2,14p' "$0"
  exit 2
fi
exec uv run python -m scripts.box.box_capture check "$@"
