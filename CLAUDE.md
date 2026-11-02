# spar-guardian-agents: rules for every session

Read `~/.claude/CLAUDE.md` and `~/.claude/OPERATIONS.md` first. This file adds what this repository needs.

## 1. Every task needs collaboration to cheat (the study's premise)

In every task, for any team of 2, 3, 4 or 10 neutral agents and every seed, no single agent's own held items
plus its own honest work may pass the grader or complete the exploit. The exploit needs items held by at
least two different agents. All holdings together must pass.

- `tests/unit/test_collaboration_*.py` prove it per task with no model call. Run them before any launch:
  `uv run pytest tests/unit -k collaboration -q`.
- A grading shortcut (added files, hints, a softer check) is a change to the exploit. Rerun the tests.
- Never record a task property that breaks this rule as a fact in a doc. Stop and tell the user.
- On 2026-11-01 README.md said "In SWE an agent can still use its own piece" and nobody stopped. Every SWE
  run of study_exploration_1 ran on a task where one piece passed and the grader's added files let honest
  teams pass. The whole target's data lost its meaning.

## 2. No rented box sits idle (vast.ai, RunPod)

On 2026-11-01, 19 boxes sat idle 8.2 hours at $15.40/h, about $126, after the OpenRouter key hit its limit
and the session ended its turn. The guard in `~/.claude/hooks/cloud_boxes_guard.py` now enforces:

- Before `fleet create`: `python3 ~/.claude/hooks/cloud_boxes_guard.py start-daemon --project "$PWD"`.
  `fleet create` and the PreToolUse hook both refuse without the daemon. launchd keeps it alive.
- The daemon measures each box through `.claude/box_progress.sh` (Inspect's traces, the runner found in /proc,
  the box's own key) and tears down an idle one through `.claude/box_teardown.sh`; a box it cannot capture is
  stopped, not destroyed, and escalated with an on-screen dialog.
- While any box exists: `/loop 30m /cloud-boxes`, and act on every check. The Stop hook refuses to end a
  turn with boxes billing unless the daemon is alive, its status is fresh and no box is idle.
- A box whose runs cannot progress is idle: key limit, provider outage, broken task, runs all ended.
  Capture and destroy it in the same turn: `.claude/box_teardown.sh vast <id>`.
- `.claude/box_progress.sh` and `.claude/box_teardown.sh` are the guard's adapters. Keep them working.
- Report idle time as money, in the user's terms, at once.

## 3. Money and keys

- OpenRouter only, key as an env variable from `~/.zshrc` (`zsh -lic`), never printed, never on a command
  line; it reaches a box only through ssh stdin into `/root/.box_env`.
- The GitHub token never goes to a box.
- No paid step the user did not ask for: no judge, no ladder, no re-run, no rental.
- Before any launch tell the user the cost per cell and the fleet's $/h, in a short table.
- Before every paid step and every 30 minutes during one: `python3 ~/.claude/hooks/cloud_boxes_guard.py spend`,
  and its table goes to the user (spent today, last hour, burn $/h, balances). The hook refuses a rental or a
  model run without a spend check in the last 30 minutes.

## 3a. No run spends past its cap (enforced in code since 2026-11-02)

On 2026-11-01 eight Opus and Sonnet regulator runs cost $176.56 in thirty minutes. The estimate came from a pilot
with other workers, Claude's history was never cached, and nothing priced the live runs. These now hold, each in
code with tests that fail when it breaks:

- Every paid run has a dollar cap that Inspect enforces: `SwarmConfig.cost_limit_usd`, $5 by default
  (`src/models/cost_cap.py`). `run.run` refuses a paid model without a registry price and a paid run without a
  cap. A higher cap only with the user's yes: `--set cost_limit_usd=X`.
- A box gets only `BOX_OPENROUTER_API_KEY`, a key with a credit limit set in OpenRouter's dashboard for that
  launch. `scripts/box/box_key.py` refuses no key, the main key, a key without a limit, or one with less left than
  the box's runs may spend at their caps. `--allow-main-key` is gone. On 2026-11-02 the user ordered the other way for
  study_exploration_1: "Capped key NO, JUST USE THE KEY WE HAVE". `box_run.sh <id> --main-key` sends the main key
  under the same limit checks. Any other study needs the user's word again.
- Claude through OpenRouter is refused until `scripts/check_anthropic_cache.py` (two calls, under one cent, only
  with the user's yes) shows the cache is read and `src/models/model_registry.yaml` records `caching_verified`.
- An estimate comes from a pilot with the same models in the same roles. In the first 10 minutes of every new
  cell, price the live runs: `uv run python -m scripts.box.live_cost <box ids>`, and again with `--since`. Stop
  any run running past its estimate and tell the user.
- The guard escalates when model spend runs above $50/h over 15 minutes. Act on it at once.

## 4. Boxes hold the only copy

Destroy only through `scripts/box/fleet destroy`, which needs a passing `capture_check.sh --pull`. Never
destroy another workstream's box. Never edit a running shell script in place.

## 5. Code

Never edit `submodules/`. Never restore `docs/`. Never write `OLD/`. Read a file right before editing it.
Commit each verified unit on main and push. Commit messages end with the Co-Authored-By line.
