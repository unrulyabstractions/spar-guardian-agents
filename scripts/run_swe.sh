#!/usr/bin/env bash
# Run one arm of Lucy's plan on a CheatBench SWE task with Gemini 3.8 Flash, then convert, judge,
# compute the rungs and the summary.
#
#   scripts/run_swe.sh <agent|board> <run_name> [extra -T args...]
#   e.g. scripts/run_swe.sh board swe_flipt_n2 -T task=flipt-02e2 -T n_agents=2
#
# Writes logs/swe/<run_name>/*.eval and runs/<run_name>/ (judge.jsonl, rungs.json, swe_summary.json).
# MODEL_TIMEOUT_S (default 2700) bounds one model request including its retries.
# The body is one function, so bash has read all of it before it runs: editing this file while a run
# is in progress cannot change what that run executes.
set -euo pipefail

main() {
  local ROOT KIND NAME TASK_FN LOG_DIR EVAL_LOG
  ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  cd "$ROOT"
  KIND="$1"; NAME="$2"; shift 2
  case "$KIND" in
    agent) TASK_FN="cheatbench_swe_agent" ;;
    board) TASK_FN="cheatbench_swe_board" ;;
    *) echo "first argument must be agent or board" >&2; exit 2 ;;
  esac
  : "${GEMINI_API_KEY:?GEMINI_API_KEY must be set}"
  : "${OPENAI_API_KEY:?OPENAI_API_KEY must be set for the judge}"
  export GOOGLE_API_KEY="${GOOGLE_API_KEY:-$GEMINI_API_KEY}"
  LOG_DIR="$ROOT/logs/swe/$NAME"
  if [ -e "$ROOT/runs/$NAME" ] || [ -e "$LOG_DIR" ]; then
    echo "runs/$NAME or logs/swe/$NAME exists; choose a new run name" >&2; exit 2
  fi
  mkdir -p "$LOG_DIR"
  # Keep the Mac from idle-sleeping while this run is alive (a 16-minute idle sleep stalled the first pilot).
  if command -v caffeinate >/dev/null; then caffeinate -i -w $$ & fi
  echo "== eval $TASK_FN $* -> $LOG_DIR"
  "$ROOT/.venv/bin/inspect" eval "$ROOT/integration/task/swe_agent.py@$TASK_FN" \
    --model google/gemini-3.8-flash --log-dir "$LOG_DIR" --display plain \
    --timeout "${MODEL_TIMEOUT_S:-2700}" "$@"
  EVAL_LOG="$(ls -t "$LOG_DIR"/*.eval | head -1)"
  [ -f "$EVAL_LOG" ] || { echo "no eval log written" >&2; exit 3; }
  "$ROOT/scripts/judge.sh" "$EVAL_LOG" "$NAME"
  "$ROOT/.venv/bin/python" -m integration.checks.rungs "$ROOT/runs/$NAME"
  "$ROOT/.venv/bin/python" -m integration.report.swe_summary "$ROOT/runs/$NAME"
}

main "$@"
