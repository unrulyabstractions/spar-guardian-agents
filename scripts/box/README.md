# scripts/box

These scripts run study_exploration_1 on rented vast.ai boxes. `study_table.py` states the study: its targets, regulators, arms and boxes. A box bills by the hour and holds the only copy of what it makes. Every instance therefore enters the registry the moment it exists, and a box is destroyed only after a check proves that this machine holds every file it made.

You launch each stage by hand and by name: `pilot`, `gate`, or one `phase2-<target>`. Nothing starts one stage from another. No judge runs on a box. The code ladder is every run's result.

## No box without the guard

On 2026-11-01, 19 boxes sat idle for 8.2 hours at $15.40/h, about $126, after the OpenRouter key hit its limit and nothing watched them. The cloud box guard, `~/.claude/hooks/cloud_boxes_guard.py`, now stands between every rental and that loss:

```
python3 ~/.claude/hooks/cloud_boxes_guard.py start-daemon --project "$PWD"   # before any create; create refuses without it
python3 ~/.claude/hooks/cloud_boxes_guard.py status                           # the boxes, their $/h, the daemon
python3 ~/.claude/hooks/cloud_boxes_guard.py check --project "$PWD"           # one pass: measure, tear down the idle
```

The daemon asks `.claude/box_progress.sh` every 10 minutes how long since each box made progress (its runner alive, its newest run or log file, the key's remaining limit). A box idle for 30 minutes goes through `.claude/box_teardown.sh`: `capture_check.sh --pull`, then `fleet destroy`, both of which refuse on their own. The daemon posts a macOS notification on idle, on teardown and on any failure. In a Claude Code session, `/loop 30m /cloud-boxes` runs the same check, and the Stop hook refuses to end a turn with boxes billing unless the daemon is alive, its status is fresh and no box is idle.

## Set up

Read what a stage runs. This prints every box, run name and flag, and it sends nothing:

```
uv run python -m scripts.box.fleet jobs             # the whole study
uv run python -m scripts.box.fleet jobs pilot       # one stage
```

Make one OpenRouter key for this study with a spend cap, and revoke it after the destroys. Every model of the study routes through OpenRouter, so a box holds this one key. Set it in the shell that runs `box_run.sh`, typed at a silent prompt so it stays out of the shell's history:

```
read -rs BOX_OPENROUTER_API_KEY && export BOX_OPENROUTER_API_KEY
```

The key reaches a box through ssh's standard input, into `/root/.box_env` with mode 600. `box_run.sh --allow-main-key` sends `OPENROUTER_API_KEY` itself instead, which a third-party host then holds.

The boxes clone this repository from GitHub, so `create` refuses a commit that origin/main lacks. Push first, or pin what origin holds with `--commit origin/main`.

Each stage then rents and sets up its boxes the same way:

```
uv run python -m scripts.box.fleet offers                                   # what can hold a box, cheapest first
uv run python -m scripts.box.fleet create --stage pilot --count 3 --dry-run # the requests create would send
uv run python -m scripts.box.fleet create --stage pilot --count 3           # pins the launch, rents, registers
uv run python -m scripts.box.fleet status                                   # the account; writes the ssh configs
scripts/box/box_setup.sh <id>                                               # for each box: ssh, clone, .venv, build
```

A fix after a launch reaches its boxes through `repin`, which keeps the launch's package list. `box_setup.sh` then checks the new commit out:

```
uv run python -m scripts.box.fleet repin <id> ... --commit <sha>
```

`create` takes no offer above `--max-dph` (1.00 $/h) and stops before the fleet's $/h would pass `--max-fleet-dph` (15 $/h), its open boxes included. A first boot takes up to 25 minutes, and `box_setup.sh` waits 30. Running `box_setup.sh` again follows a setup alive on the box.

A box vast.ai cannot start shows in `status` as `!!`. It bills its disk until it goes, so destroy it with `--accept-loss`, then rent its replacement:

```
uv run python -m scripts.box.fleet create --stage pilot --count 1 --only <box> --label-prefix spar-screen-retry
```

A box whose VM comes up smaller than its offer is refused by `box_setup.sh`. Capture and destroy it, then rent its replacement with `--skip-machine <machine id>`, which `offers` prints beside each offer.

## Mock check

Rehearse each box before it spends anything. The mock check runs each arm of the box once on Inspect's mock model. No key goes to the box, nothing is paid, and the run names end in `mock<k>`, so they never stand in for real runs.

```
scripts/box/box_run.sh <id> --mock
scripts/box/watch.sh --once <id>
```

Before the first fleet with the access log, one box of each task runs the check in `src/domains/README.md` (A first real box).

## Pilot

The pilot has 3 boxes and 9 runs: flipt, protein and creative on Luna workers, with each of the 3 regulators at `ambiguous`. Rent and set up as above, then start each box and watch:

```
scripts/box/box_run.sh <id>
scripts/box/watch.sh
```

`watch.sh` pulls each box's `runs/`, `logs/` and `out/` every 3 minutes and prints its runs. It flags a box that cannot be reached, a run in trouble, and a box whose runs have all ended, since that box bills until it is captured and destroyed. It never starts, stops or destroys anything.

`box_run.sh` on a box whose runner ended starts what is not finished. A run's arguments live in the box's plan, and the runner refuses a resume with other arguments. It stops a hung run and records it. A run that lacks only its code ladder gets it rebuilt, with no model call.

Capture and destroy the pilot's boxes, then read its runs before the gate.

## Gate

The gate has 6 boxes and 40 runs: every target at `minimal` and `minimal_cheat`, seeds 1 to 5.

```
uv run python -m scripts.box.fleet jobs gate
uv run python -m scripts.box.fleet create --stage gate --count 6
```

Then `status`, `box_setup.sh`, the mock check and `box_run.sh` as above.

## Phase two

Read the gate first. You then launch each target on its own, and nothing automatic starts one. Each target has 40 runs: `minimal` at rule levels `generic` and `identified`, and `minimal_regulated` with each regulator at `ambiguous` and `enforce`.

| stage | boxes |
|---|---|
| `phase2-flipt-dsflash` | 8 |
| `phase2-flipt-luna` | 8 |
| `phase2-protein-luna` | 7 |
| `phase2-creative-luna` | 7 |

```
uv run python -m scripts.box.fleet jobs phase2-flipt-luna
uv run python -m scripts.box.fleet create --stage phase2-flipt-luna --count 8
```

Then `status`, `box_setup.sh`, the mock check and `box_run.sh` as above.

## Capture

Prove that this machine holds every file the box made. `--pull` fetches what is missing or different, then sweeps again:

```
scripts/box/capture_check.sh <id> --pull
```

The check compares size and sha256 for every regular file under the clone, `/root` and `/tmp`. It counts what is provably reproducible and requires the rest. It fails on a file missing or different, on a process still producing, on a link out of the swept roots, and on Docker holding containers or volumes unless `--accept-docker REASON` says why they may go. `--pull` never overwrites a newer local file: the box's bytes go to `logs/boxes/<id>/conflicts/`.

## Destroy

```
uv run python -m scripts.box.fleet destroy <id> --dry-run   # every gate and the snapshot, no destroy
uv run python -m scripts.box.fleet destroy <id>
```

`destroy` needs a passing capture from the last 10 minutes and runs the check once more. It refuses a box outside the registry or under another label. It snapshots the captured files, reads the snapshot back, destroys, and marks the box destroyed only once the account no longer lists it. The entry for `VERIFICATION_LOG.md` goes to `logs/boxes/destroyed.jsonl`.

`destroy <id> --accept-loss REASON` is for a box that can never pass the capture. It keeps the registry and label gates and logs the reason with what the check found lost.
