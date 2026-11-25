# scripts/box

These scripts run a study's plan (`studies/plans/<study>.yaml`, with its gated spec in `studies/gated/` when it has
one) on rented vast.ai boxes. Each box runs the Mac's own launcher, `scripts/study/run_study.py`, on some of the plan's
targets, and its runs come back into this machine's `runs/<study>/` and `logs/<study>/` as if they had run here.
A box bills by the hour and holds the only copy of what it makes. Every instance enters the registry
(`runs/boxes.json`) the moment it exists, and a box is destroyed only after a check proves this machine holds every
file it made.

## The commands, in order

```
python3 ~/.claude/hooks/cloud_boxes_guard.py start-daemon --project "$PWD"     # 1. the guard; create refuses without it
python3 ~/.claude/hooks/cloud_boxes_guard.py status
python3 ~/.claude/hooks/cloud_boxes_guard.py spend                            #    spent today, last hour, $/h, balances
uv run python -m scripts.box.fleet plan <plan> [--gates <spec>] --boxes N     # 2. the assignment; sends nothing
uv run python -m scripts.box.fleet offers                                     # 3. what can hold a box, cheapest first
uv run python -m scripts.box.fleet create --plan <plan> [--gates <spec>] --boxes N --dry-run
uv run python -m scripts.box.fleet create --plan <plan> [--gates <spec>] --boxes N
uv run python -m scripts.box.fleet status                                     # 4. the account; writes each ssh config
scripts/box/box_setup.sh <id>                                                 # 5. per box: clone, .venv, kernel, build
scripts/box/box_run.sh <id> --mock                                            # 6. the mock check: no key, no spend
scripts/box/watch.sh --once <id>
scripts/box/box_run.sh <id> --main-key                                        # 7. the real runs, on the main key
scripts/box/watch.sh                                                          # 8. pulls runs/ and logs/ every 3 min
uv run python -m scripts.box.live_cost <id> ... --save snap.json              #    the live runs' cost so far
uv run python -m scripts.box.live_cost <id> ... --since snap.json             #    and each run's $/h since then
scripts/box/capture_check.sh <id> --pull                                      # 9. every file here, by bytes
uv run python -m scripts.box.fleet destroy <id>                               # 10. snapshot, then destroy
uv run python -m scripts.box.fleet stop <id>                                  #     a stop alone: the disk stays
```

`fleet plan` prints each box: its groups of targets, its runs, its agents at once and the sum of its runs' dollar caps,
then the study's total. A gate (the spec's `when`) joins its arms onto one box, since it reads its earlier arm's stored
run there. Groups go to the boxes heaviest first, each to the box with the fewest agents. A box's agents at once are
`--max-agents`, else the smaller of 1.4 per core (the Mac's figure; no box has measured it) and 60% of its memory over
the plan's `limits.needs.<task>.memory_gb` per agent.

`create` pins the commit first and reads the assignment, the caps and the keys from the plan and spec as that commit
holds them, since that is what the boxes clone. It rents the assignment's boxes and records, per box, the study, the
plan, the spec, its targets, its agents at once, its runs, their caps and the commit. `box_run.sh` reads them back and
starts, under `nohup setsid` with `/root/.box_env` sourced:

```
.venv/bin/python -m scripts.study.run_study <plan> --yes [--gates <spec>] --only <its targets> --limit max_agents=<N>
```

`--mock` adds `--mock`: every model is Inspect's mock model, the runs go to `<study>_mock` under names that end in
`_mock`, so no tool that matches names can take a rehearsal for a real run, and no key goes to the box. The launcher
decides no gates under `--mock`, so a mock launch drops `--gates` and names only the box's targets the pinned plan
holds. Running `box_run.sh` again after the launcher ended starts the runs the box does not store.

A target runs on the Mac or on one box, never both. `create` and `box_run.sh` refuse while a launcher of the study
lives on the Mac, and a launcher started on the Mac leaves out every target an open box of the study runs (its header
names each box). A run name with any trace on the Mac (its run folder, an eval log, its console log) is refused by both
before any rent or launch; set the old attempt aside first.

Every study's boxes take the main key (CLAUDE.md §3a): `--main-key` sends `OPENROUTER_API_KEY` from the shell's
environment, so run `box_run.sh` from a terminal that reads `~/.zshrc`, or as `zsh -lic 'scripts/box/box_run.sh <id>
--main-key'`. `box_key.py` checks first that OpenRouter caps the key and that enough is left for every run of every
open box that holds it, this one included, at its cap, each box's runs taken from its pinned commit; it prints the
Mac's own live runs' caps beside that, and records that the box holds the key. Without `--main-key` the box gets
`BOX_OPENROUTER_API_KEY`, a key made for the launch. `repin` sets each box's runs and caps to the new commit's.

`capture_check.sh --pull` brings the box's `runs/` and `logs/` into the same folders here, its launcher output and the
plan copy it rewrote as gates opened into `logs/<study>/boxes/<id>/`, and every other file into
`logs/boxes/<id>/files/<its path>`, each compared by size and sha256. `destroy` needs that capture from the last 10
minutes, runs it once more, snapshots the captured files and reads the snapshot back. Its record goes to
`logs/boxes/destroyed.jsonl` for `VERIFICATION_LOG.md`.

## The guard

launchd keeps the guard's daemon alive (`local.cloud-boxes-guard`). Every 5 minutes it asks `.claude/box_progress.sh`,
which sends `scripts/box/box_probe.py` to the box: the launcher and its cells by their command lines in `/proc`, the
newest write under the clone's `runs/` and `logs/` and Inspect's traces, the setup, the box's own key. A live setup
reads `progress` of its log's age, and a failed one `ended setup-failed`. A box whose launcher and cells have ended
reads `ended`; one that writes nothing reads `progress` of a growing age. A launcher that cannot launch ends by itself
after 30 minutes. A box idle for 30 minutes goes through `.claude/box_teardown.sh`: the capture, then the destroy. When
the capture cannot tell what Docker holds, the teardown restarts Docker once and sweeps again; when Docker still cannot
answer, it stops the box (`fleet stop`). A box it cannot capture is stopped, not destroyed. While any box exists,
`/loop 30m /cloud-boxes` runs the same check.

## What each refusal means

| refusal | meaning, and what to do |
|---|---|
| `no cloud box guard daemon` | start the daemon (step 1); a dry run needs none |
| `is not on origin/main` | the boxes clone from GitHub: push first, or pin `--commit origin/main` |
| `N groups of targets for M boxes` | fewer boxes, since a box would run nothing |
| `no memory need per agent` | add `limits.needs.<task>.memory_gb` to the plan, or pass `--max-agents` |
| `needs OPENAI_API_KEY, and a box holds only OPENROUTER_API_KEY` | a run's model, a role's too, must route through OpenRouter |
| `<plan> here differs from commit <sha>` | the boxes run the commit's plan and spec: commit and push the file, or restore it |
| `<run> has traces here` | a run folder, eval log or console log of that name is here: set it aside, or leave its target out |
| `a launcher of <study> is live on this machine` | stop the Mac's launcher first; one started later leaves out the boxes' targets |
| `box <id> of <study> runs <targets> already` | two boxes of one study never run the same target |
| `the fleet would bill $X/h, past its cap` | `--max-fleet-dph` (15 $/h) holds the open boxes and this launch |
| `the account holds these boxes already` | a box of this name is live: destroy it, or rent under another `--label-prefix` |
| `vast.ai cannot start it` (status) | destroy it with `--accept-loss`, then `create --plan <plan> [--gates <spec>] --boxes N --only <box> --label-prefix spar-screen-retry` |
| `the box needs N cores` (setup) | the VM came up smaller than its offer: capture, destroy, rent with `--skip-machine <machine>` |
| `<run> has traces here` (run-args) | a trace the box did not make; a failed box run's pulled traces count too: set them aside |
| `no longer holds the box's targets` (run-args, repin) | the commit's plan cut or dropped a target of the box: pin a commit that holds them |
| `no box key`, `no credit limit`, `less than the $X the runs of the boxes that hold it may spend` | the key sent (`OPENROUTER_API_KEY` with `--main-key`, else `BOX_OPENROUTER_API_KEY`) must be capped in OpenRouter with room for every run of every open box that holds it, gated arms too |
| `a launcher is alive` | the box runs already; watch it |
| `/root/box_id names ...` | the endpoint reaches another box: run `fleet status` |
| `this machine holds another run of <run>` (capture) | an eval log here, or the one a run.json here names, is not the box's: nothing is pulled; set the local run aside first |
| `Docker holds ... containers` (capture) | a stopped run's workspace: `box_teardown.sh` saves it under `/root/docker_saved` and passes `--accept-docker` |
| `cannot tell what Docker holds` (capture) | `box_teardown.sh` restarts Docker once; if it still cannot answer, the box is stopped for a person |
| `STUCK nothing runs and ...` (launcher) | the launcher ended with 3: memory or Docker's disk unreadable, or a floor above the disk |
| `box <id> runs <targets>: left out here` (launcher header) | the Mac leaves those targets to their box |

## Checks on one box

`box_setup_remote.sh` runs `proc_hiding_check.py`, which proves the runs' `/proc` hiding on the box's kernel and
refuses the box when it fails. `network_trace_check.sh` says which way of recording the agents' network calls works
there (eBPF or the socket tables). It tests in `cbtask:python311`, which a box builds only for creative_writing or
protein_design; a SWE box builds it first with the first line below (the SWE image has no bash):

```
scripts/box/box_ssh.sh <id> "cd /root/work && .venv/bin/python -c \
  'from src.sandbox.machines import agent_machines; agent_machines.ensure_image()'"
scripts/box/box_scp.sh <id> scripts/box/network_trace_check.sh box:/root/ship/
scripts/box/box_ssh.sh <id> bash /root/ship/network_trace_check.sh --json /root/network_trace_check.json
```
