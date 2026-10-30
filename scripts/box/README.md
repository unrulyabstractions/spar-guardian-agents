# scripts/box

These scripts run a screen on rented vast.ai boxes, one box per (task, model). A box bills by the hour and holds the only copy of what it makes. Every instance is therefore registered the moment it exists, and a box is destroyed only after a check proves that this machine holds every file it made.

## From offer to destroy

```
uv run python -m scripts.box.fleet offers                       # the offers that can hold a box, cheapest first
uv run python -m scripts.box.fleet create --count 15 --dry-run  # the requests create would send
uv run python -m scripts.box.fleet create --count 15            # rents and registers (runs/boxes.json)
uv run python -m scripts.box.fleet status                       # the account with the registry; writes the ssh configs
scripts/box/box_setup.sh <id> <task>                            # the clone, the .venv, the task's rows and images
scripts/box/box_run.sh <id> <task> <model> <suffix>             # the key, then every cell x 3 samples at once
scripts/box/watch.sh                                            # every 3 minutes: pull runs/ logs/ out/, print each box
scripts/box/capture_check.sh <id> --pull                        # every file on the box has a local copy
uv run python -m scripts.box.fleet destroy <id>                 # only after a fresh capture check
```

The pilot is one box per task on one model, with one sample of two cells: `create --count 5 --models deepseek-flash-openrouter`, then `box_run.sh <id> <task> deepseek-flash-openrouter pilot --samples 1 --kinds solo_cheat minimal_regulated`.

| file | what it does | bills or destroys |
|---|---|---|
| `fleet.py` | offers, create, status, ids and destroy against the vast.ai API; the registry; the destroy gates | create bills, destroy destroys |
| `fleet_layout.py` | the tasks, models and cell kinds, the labels, the paths; standard library only | no |
| `box_ssh.sh`, `box_scp.sh` | ssh and scp to a box by its id, through `logs/boxes/<id>/ssh_config` | no |
| `box_setup.sh` | the baseline of the host image, the clone at a pushed commit, lucy as a bundle, the .venv from the Mac's package list, the task's build | no |
| `box_run.sh` | the model's key through ssh's standard input, then the runner under `nohup setsid` | no |
| `box_runner.py` | on the box: build a task, run its cells with the hung-run guard, report status | no |
| `watch.sh` | pulls each box's outputs and prints its runs; never starts, stops or destroys | no |
| `capture_check.sh`, `box_capture.py`, `box_sweep.py` | the sweep of the box and its comparison with this machine; the marker destroy needs | no |

## The rules the scripts hold

- create searches and rents in one step, since offer ids expire within minutes. It refuses a count that does not match the boxes asked for, labels the account holds already, and offers dearer than `--max-dph` (1.00 $/h by default). Each instance enters the registry when vast.ai returns its id. A failure that may have created an instance stops the launch, and an audit of the account registers what this launch asked for and fails on any other spar-screen instance outside the registry.
- A dry run sends only reads. Its client refuses any other request.
- The API key goes only into the Authorization header. A model key goes only through ssh's standard input into `/root/.box_env` (mode 600). No GitHub token goes to a box.
- The box clones this repository at a commit origin/main holds, and cheatbench and orbit from GitHub. Lucy's private repository goes up as a bundle. The Mac's uplink is slow, so nothing large goes up.
- The runner stops a run that is alive 20 minutes past Inspect's time limit with nothing written for 20 minutes, or alive at twice that limit, and records it as hung.
- The capture sweeps every regular file under the clone, `/root` and `/tmp`, without following links, and compares size and sha256. It counts, and does not require, what is provably reproducible: tracked files unchanged at their work tree's HEAD, git's stores, the .venv, `build/`, the editable install's metadata, package, font and tiktoken caches, uv's Pythons, binaries and receipt, the bundle and package list, bytecode caches, the keys file, `authorized_keys`, and the host image's own files from the baseline. Docker's images and volumes and apt's caches lie outside the swept roots. The check fails while a runner or an example still runs.
- destroy refuses a box outside the registry, a label other than `spar-screen-*`, and a capture marker older than 10 minutes. It runs the capture check once more, destroys, waits until the account no longer lists the box, and appends the entry for `VERIFICATION_LOG.md` to `logs/boxes/destroyed.jsonl`.
