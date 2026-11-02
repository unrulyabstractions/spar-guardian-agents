# scripts/box

These scripts run study_exploration_1 on rented vast.ai boxes. `study_table.py` states the study: its targets, regulators, arms and boxes. A box bills by the hour and holds the only copy of what it makes. Every instance therefore enters the registry the moment it exists, and a box is destroyed only after a check proves that this machine holds every file it made.

You launch each stage by hand and by name: `pilot`, `gate`, one `phase2-<target>`, one `phase2b-<target>`, `fixed-pilot`, `fixed-gate`, one `fixed-phase2-<target>`, `vuls-gate`, one `fixed-fill-<target>`, one `fixed-fill-argon-<target>`, `disreg-gate`, one `disreg-<target>` or one `contest-<target>`. Nothing starts one stage from another. No judge runs on a box. The code ladder is every run's result.

## Stages

A target is a task and its workers' model: `flipt-dsflash`, `flipt-luna`, `flipt-haiku`, `protein-luna` or `creative-luna`. The first regulators are Luna, Opus and Argon. Phase 2b adds Haiku and Sonnet on boxes of its own. Phase two's boxes keep their arms.

| stage | what it runs |
|---|---|
| `pilot` | flipt, protein and creative on Luna: `minimal_regulated`, each first regulator at `ambiguous`, seed 1 |
| `gate` | every target: `minimal` and `minimal_cheat`, seeds 1 to 5 |
| `phase2-<target>` | `minimal` at rule levels `generic` and `identified`, and `minimal_regulated` with each first regulator at `ambiguous` and `enforce`, seeds 1 to 5; on SWE an Opus regulator takes seed 1 alone |
| `phase2b-<target>` | `minimal_regulated` with Haiku at `ambiguous` and `enforce`, seeds 1 to 5, and with Sonnet at both, seed 1 |
| `fixed-pilot` | flipt on Luna after the SWE fix: `minimal_regulated`, each of the five regulators at `ambiguous`, seed 1 |
| `fixed-gate` | both flipt targets after the SWE fix: `minimal` and `minimal_cheat`, seeds 1 to 3 |
| `fixed-phase2-<target>` | a flipt target whose fixed gate passed: `minimal` at `generic` and `identified`, and each of the five regulators at `ambiguous` and `enforce`, seeds 1 to 3; Opus and Sonnet take seed 1 |
| `vuls-gate` | SWE's second issue, vuls-1832, on DeepSeek and Luna: `minimal` and `minimal_cheat`, seeds 1 to 3. Its pieces hold the collaboration rule like flipt's, and the runner builds its images before the runs start |
| `fixed-fill-<target>` | what the DeepSeek SWE table lacks, each run with 90 minutes and tag `fill`: `minimal_regulated` with GPT-6 Luna at `ambiguous`, seeds 1 to 3, and at `enforce`, seeds 2 and 3; Sonnet at both levels, seed 1 (cap $15); Gemini 4 Argon at `ambiguous`, seed 1 (cap $30), which prices the next stage |
| `fixed-fill-argon-<target>` | the other Argon runs, launched only after that run gives their price: `ambiguous`, seeds 2 and 3, and `enforce`, seeds 1 to 3 (cap $30) |
| `disreg-gate` | flipt on Claude Haiku 5.5 workers: `minimal` and `minimal_cheat`, seeds 1 to 3. `gate_readout` reads it by its own rule, and that target's next stages run only if it passes |
| `disreg-<target>` | `flipt-haiku` or `creative-luna`: `minimal_disregulated` at `persuade` and `overt`, seeds 1 to 3. The disregulator runs the workers' model and holds no piece |
| `contest-<target>` | the same targets: `minimal_regulated_disregulated`, `persuade` against a Haiku regulator at `enforce`, seeds 1 to 3 |

Every SWE run before the fix (commits 7331882 to 33b7810, 2026-11-01) graded a task where one agent could cheat alone. Such a run measures nothing about collaboration. The fixed stages redo SWE. The fixed gate's and fixed phase two's runs carry the tag `fixed`, and the fixed pilot's the tag `fixedpilot`. No fixed run's name repeats an invalid run's.

`fleet jobs <stage>` lists a stage's boxes and runs. The table gives their counts.

| stage | boxes | runs |
|---|---|---|
| `pilot` | 3 | 9 |
| `gate` | 6 | 40 |
| `phase2-flipt-dsflash` | 6 | 32 |
| `phase2-flipt-luna` | 6 | 32 |
| `phase2-protein-luna` | 7 | 40 |
| `phase2-creative-luna` | 7 | 40 |
| `phase2b-flipt-dsflash` | 3 | 12 |
| `phase2b-flipt-luna` | 3 | 12 |
| `phase2b-protein-luna` | 2 | 12 |
| `phase2b-creative-luna` | 2 | 12 |
| `fixed-pilot` | 1 | 5 |
| `fixed-gate` | 2 | 12 |
| `fixed-phase2-flipt-dsflash` | 7 | 28 |
| `fixed-phase2-flipt-luna` | 7 | 28 |
| `vuls-gate` | 2 | 12 |
| `fixed-fill-flipt-dsflash` | 2 | 8 |
| `fixed-fill-argon-flipt-dsflash` | 1 | 5 |
| `disreg-gate` | 1 | 6 |
| `disreg-flipt-haiku` | 2 | 6 |
| `disreg-creative-luna` | 1 | 6 |
| `contest-flipt-haiku` | 1 | 3 |
| `contest-creative-luna` | 1 | 3 |

## No box without the guard

On 2026-11-01, 19 boxes sat idle for 8.2 hours at $15.40/h, about $126, after the OpenRouter key hit its limit and nothing watched them. The cloud box guard, `~/.claude/hooks/cloud_boxes_guard.py`, now stands between every rental and that loss:

```
python3 ~/.claude/hooks/cloud_boxes_guard.py start-daemon --project "$PWD"   # before any create; create refuses without it
python3 ~/.claude/hooks/cloud_boxes_guard.py status                           # the boxes, their $/h, the daemon
python3 ~/.claude/hooks/cloud_boxes_guard.py check --project "$PWD"           # one pass: measure, tear down the idle
```

launchd runs the daemon (`local.cloud-boxes-guard`): it restarts a killed daemon and starts it at login. Every 5 minutes the daemon asks `.claude/box_progress.sh` how long since each box made progress: the runner found in `/proc`, the newest Inspect trace, run folder or eval log (never the runner's plan, which it rewrites every minute), and the box's own key's remaining limit. A box no run can use for 30 minutes (10 when its key is exhausted; a new box gets an hour to boot and set up) goes through `.claude/box_teardown.sh`: `capture_check.sh --pull`, which saves a stopped run's containers under `/root/docker_saved` when Docker holds them, then `fleet destroy`. Both refuse on their own. After two failed teardowns, or 30 minutes unreachable, the daemon stops the box: its disk and data stay, and it bills storage only. Every escalation opens an on-screen dialog, which Do Not Disturb does not mute. In a Claude Code session, `/loop 30m /cloud-boxes` runs the same check, and the Stop hook refuses to end a turn while a box bills idle at its full rate.

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

The key reaches a box through ssh's standard input, into `/root/.box_env` with mode 600. Only `BOX_OPENROUTER_API_KEY` goes: a key made for one launch, with a credit limit set in OpenRouter's dashboard. `scripts/box/box_key.py` refuses it unless OpenRouter caps it, it is not the main key, and it has enough left for the box's runs at their dollar caps (each run's `cost_limit_usd`, $5 by default). `--allow-main-key` is gone.

On 2026-11-02 the user ordered the main key for study_exploration_1: `scripts/box/box_run.sh <id> --main-key` sends `OPENROUTER_API_KEY` instead, and the key check still needs its credit limit and enough left. Any other study needs the user's word again.

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

Rent and set up the pilot's boxes as above, then start each box and watch:

```
scripts/box/box_run.sh <id>
scripts/box/watch.sh
```

`watch.sh` pulls each box's `runs/`, `logs/` and `out/` every 3 minutes and prints its runs. It flags a box that cannot be reached, a run in trouble, and a box whose runs have all ended, since that box bills until it is captured and destroyed. It never starts, stops or destroys anything.

`box_run.sh` on a box whose runner ended starts what is not finished. A run's arguments live in the box's plan, and the runner refuses a resume with other arguments. It stops a hung run and records it. A run that lacks only its code ladder gets it rebuilt, with no model call.

Capture and destroy the pilot's boxes, then read its runs before the gate.

## Gate

Rent the gate's boxes:

```
uv run python -m scripts.box.fleet jobs gate
uv run python -m scripts.box.fleet create --stage gate --count 6
```

Then `status`, `box_setup.sh`, the mock check and `box_run.sh` as above. The readout gives each target's verdict from the runs on this machine:

```
uv run python -m scripts.box.gate_readout gate
```

## Phase two

Read the gate first. You then launch each target on its own, and nothing automatic starts one:

```
uv run python -m scripts.box.fleet jobs phase2-flipt-luna
uv run python -m scripts.box.fleet create --stage phase2-flipt-luna --count 6
```

Then `status`, `box_setup.sh`, the mock check and `box_run.sh` as above.

## Phase 2b

Each target's phase 2b runs on boxes of its own, apart from phase two's:

```
uv run python -m scripts.box.fleet jobs phase2b-flipt-luna
uv run python -m scripts.box.fleet create --stage phase2b-flipt-luna --count 3
```

Then `status`, `box_setup.sh`, the mock check and `box_run.sh` as above.

## Fixed SWE stages

`fixed-phase2-<target>` is only for a flipt target whose fixed gate passed. The readout gives that verdict:

```
uv run python -m scripts.box.fleet create --stage fixed-pilot --count 1
uv run python -m scripts.box.fleet create --stage fixed-gate --count 2
uv run python -m scripts.box.gate_readout fixed-gate
uv run python -m scripts.box.fleet create --stage fixed-phase2-flipt-luna --count 7
```

Read each stage with `fleet jobs <stage>` before its create. Run `status`, `box_setup.sh`, the mock check and `box_run.sh` after each create, as above.

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
