#!/usr/bin/env bash
# Prove that every file a box made has a local copy before the box is destroyed:
#     scripts/box/capture_check.sh <instance-id> [--pull] [--accept-docker REASON]
#
# Exit 0 only when every file holds to the rules in scripts/box/box_capture.py, which then writes the manifest and the
# marker fleet.py destroy requires from the last 10 minutes. --pull fetches what is missing or different first, then
# sweeps again. --accept-docker gives the reason Docker's containers and volumes may go with the box.
set -euo pipefail
cd "$(dirname "$0")/../.."
if [ $# -lt 1 ]; then
  sed -n '2,7p' "$0"
  exit 2
fi
exec uv run python -m scripts.box.box_capture check "$@"
