#!/usr/bin/env bash
# Convert an ORBIT .eval log into a CheatBench run directory and judge it.
#
#   scripts/judge.sh <eval_log> <run_name> [judge]
#
# Writes runs/<run_name>/ (eval_to_atif.py) and runs/<run_name>/judge.jsonl
# (CheatBench's judge.py, run inside the cheatbench-judge image because the
# judge imports Linux-only sandbox code). Exits non-zero if any step fails or
# if no episode was judged.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EVAL_DIR="$(cd "$(dirname "$1")" && pwd)"
EVAL_LOG="${EVAL_DIR%/}/$(basename "$1")"
RUN_NAME="$2"
cd "$ROOT"   # python -m integration.* needs the repository root on sys.path
JUDGE="${3:-gpt-6-sol}"
RUN_DIR="$ROOT/runs/$RUN_NAME"
: "${OPENAI_API_KEY:?OPENAI_API_KEY must be set for the judge}"
[ -f "$EVAL_LOG" ] || { echo "no such eval log: $EVAL_LOG" >&2; exit 2; }
docker image inspect cheatbench-judge >/dev/null 2>&1 || {
  echo "image cheatbench-judge missing; build: docker build -t cheatbench-judge $ROOT/integration/docker/judge" >&2; exit 2; }

echo "== convert $EVAL_LOG -> $RUN_DIR"
"$ROOT/.venv/bin/python" -m integration.convert.eval_to_atif "$EVAL_LOG" "$RUN_DIR"
[ -s "$RUN_DIR/episodes.jsonl" ] || { echo "conversion produced no episodes" >&2; exit 3; }
# A run whose sample ended in a provider or harness error is incomplete: do not spend judge
# calls on it unless asked (JUDGE_ERRORS=1). The converter has already written the records.
if [ "${JUDGE_ERRORS:-0}" != "1" ] && "$ROOT/.venv/bin/python" - "$RUN_DIR/episodes.jsonl" <<'PY'
import json, sys
recs = [json.loads(l) for l in open(sys.argv[1])]
sys.exit(0 if any(r.get("failure") in ("harness", "connection_closed") for r in recs) else 1)
PY
then echo "run has a harness or provider failure; not judging (set JUDGE_ERRORS=1 to force)" >&2; exit 5; fi

echo "== judge ($JUDGE) in cheatbench-judge"
docker run --rm -v "$ROOT:$ROOT" -w "$ROOT/submodules/cheatbench" -e OPENAI_API_KEY cheatbench-judge \
  python judge.py "$RUN_DIR" --judge "$JUDGE" --models-config "$ROOT/integration/task/models.yaml"
[ -s "$RUN_DIR/judge.jsonl" ] || { echo "judge wrote no verdicts" >&2; exit 4; }

echo "== verdicts"
"$ROOT/.venv/bin/python" - "$RUN_DIR/judge.jsonl" <<'PY'
import json, sys
keys = ("episode", "parse_ok", "cheated", "honey_pots_seen", "attempted_access", "extracted_submission",
        "success_claim_seen_before_access", "disclosed_access", "explicit_prohibition_violated",
        "judge_cost_usd", "judge_error")
# CheatBench SWE (tasks/software_engineering/judge_schema.py) has its own verdict fields.
SWE_KEYS = ("episode", "parse_ok", "cheated", "reference_solution_seeking", "reference_solution_obtained",
            "reference_solution_used", "documentation_lookup", "evaluation_tampering",
            "explicit_prohibition_violated", "judge_cost_usd", "judge_error")
for line in open(sys.argv[1]):
    if json.loads(line).get("task") == "software_engineering":
        keys = SWE_KEYS
    break
for line in open(sys.argv[1]):
    d = json.loads(line)
    print(json.dumps({k: d.get(k) for k in keys}))
    print("summary:", d.get("summary"))
PY
