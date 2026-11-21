# Data and viewer

The team shares its runs through the Hugging Face dataset `unrulyabstractions/spar-swarm`. This guide shows how to pull runs, upload runs and read them in the transcript viewer. Every command runs from the repository root.

## Set up once

Clone the repository with its submodules.

```
git clone --recurse-submodules https://github.com/unrulyabstractions/spar-guardian-agents.git
cd spar-guardian-agents
```

Build the environment, `.venv`: Python 3.11 with the pinned packages of `requirements.txt` and ORBIT from its
submodule. It needs uv.

```
make setup
```

You need a Hugging Face token only to upload. Give it one way: run `hf auth login` once, or `export HF_TOKEN=<token>`, or pass `--token` to the script. The scripts never print it.

## Pull data

The dataset is public, so a pull needs no token.

```
make hf-pull STUDY=study_exploration_1 RUN=binder_minimal_cheat_luna1 DRY_RUN=1
make hf-pull STUDY=study_exploration_1 RUN=binder_minimal_cheat_luna1
```

| Variable | What it does |
|---|---|
| `STUDY` | a top-level folder of the dataset: `study_exploration_0`, `study_exploration_1` or `apart_controls` |
| `RUN` | one run, or several in quotes; without it, the whole study |
| `DRY_RUN=1` | lists the files and their size per run, and writes nothing |
| `FORCE=1` | replaces local files that differ from the Hub's |
| `DEST=<dir>` | puts `runs/` and `logs/` under another folder |

A run lands in `runs/<study>/` and its eval logs in `logs/<study>/`, where the viewer looks. A run also brings its study's index when you lack it. A local file that differs from the Hub's stops the pull before anything downloads. A run of the apart controls is `<model>/<task>/<cell>/s<seed>`, as in `RUN=ds/binder/cheat/s1`.

The same files come through the Hugging Face CLI. It keeps the dataset's layout, so you move the folders into `runs/<study>/` and `logs/<study>/` yourself.

```
hf download unrulyabstractions/spar-swarm --repo-type dataset --include "study_exploration_1/*/binder_minimal_cheat_luna1/*" --local-dir hf_data
```

## Upload

List and check first, then upload.

```
make hf-upload STUDY=study_exploration_1 RUN=<run> DRY_RUN=1
make hf-upload STUDY=study_exploration_1 RUN=<run>
```

The upload refuses any file that holds a key, the token or the git email. Without `RUN` it uploads the whole study except its smoke folders. It skips files the Hub already holds unchanged and deletes nothing there. It uploads studies only, not the apart controls. The dataset is public, so anyone can read everything you upload.

## Before you upload new runs

The viewer reads each run's ladders from its own files, so a new run shows up as it is. Three steps make it complete.

| Step | Command | Cost |
|---|---|---|
| Keep only valid runs | delete broken or partial runs, or mark them `"valid": false` in `runs/<study>/INDEX.jsonl` | none |
| Read the rungs at every call | `make replay STUDY=<study>` | none: Docker, no model call |
| Score the judge labels | `python -m src judge-pass labels runs/<study> --judge gpt-6-luna --cap <dollars> --only-missing` | paid: ask first |

`make replay` plays each run back from its recorded tool calls and puts `steps.jsonl` and `replay.json` beside the runs whose replay ends in the run's own state. A run whose replay differs is read at its end, as before. It needs Docker running and a few GB of free Docker disk. Add `RUN="<run> ..."` for some runs and `JOBS=<n>` for more at once (default 2).

Without judge scores, the viewer shows "not judged" in the Judge labels columns. Without a replay, the rungs come from the run's end state. Single-agent runs never appear in the viewer.

### The temporary directories at every call

`uv run python -m src.replay.tmp_snapshots runs/<study>/<run> --out <replay dir>` replays one run and puts `tmp_snapshots.json` beside it, only when the replay is faithful. It holds /tmp, /var/tmp and /dev/shm of every agent machine before and after every recorded call. It needs Docker, makes no model call, and refuses to replace the file without `--force`.

| Key | What it holds |
|---|---|
| `dirs`, `hash_max_bytes`, `text_max_bytes` | the directories read, and the caps: sha256 for files up to 1 MiB, text for UTF-8 files up to 8 KiB with no NUL |
| `machines` | each machine and its agents: one per agent in SWE and protein design, `default` for every agent in creative writing |
| `replay` | the replay's verdict and match counts |
| `steps` | every call in global order: `n`, `agent`, `k`, `call_id` (as in `steps.jsonl` and the viewer), `tool`, `match`, `machine`, `ran` |
| `steps[].before`, `steps[].after` | on a call that ran in a machine: `before` against the state the machine was last left in (the whole state on its first call), `after` against `before`; a changed entry whole, a removed path as `null` |

An entry is `{type, size, mode, owner, uid, mtime, sha256, text, target}`: type is file, dir, link, fifo, socket or other, mode as `ls` prints it, target for a link. A call with `"ran": false` changed nothing. The full state at a call is every `before` and `after` applied in order up to it, per machine (`decode` and `state_at` in `src/replay/tmp_snapshots.py`). Times are the replay's clock, not the run's.

The viewer's Board tab shows the file in a /tmp panel under the timeline, only on runs that have it (`/api/tmp?id=`, src/ui/API.md).

## Use the viewer

```
make transcripts
```

Open http://127.0.0.1:8770/. The viewer runs until Ctrl-C. `make transcripts REBUILD=1` measures every run again. For another port, run `uv run python -m src.ui.transcript_viewer --port <port>`. The viewer lists valid runs only. Board and Details appear on runs with more than one agent.

| Page or tab | What it holds |
|---|---|
| Runs | One row per run. Filters in three groups: Run (Search, Task, Workers, Collection), Who (Workers' model, Worker conditioning, Influencer, Influencer conditioning) and Outcome (Cheating, Cheated, Coordination, Collusion, Disclosure at least, Manipulation at least). The default order is Interest: the team's Cheating rung against its influencers, rank 1 first. A column head sorts by that column. |
| Agents | Two selects, Workers and Influencers, each ordered by Cheating, then Coordination, then Collusion, highest first; the selected agent shows its whole episode, and a run opens on the first worker, one step per model turn from 1, each with its labels: the turn's reasoning, then its calls, which a button on the step folds at once. The System box also holds the tool definitions sent with every model call, from the eval log. Steps never fold. The bar's toggles fold every call, the System prompt, every User message and every Reasoning section; the System prompt and the User messages start folded. A tool result starts folded and a tool call open, each folding on its own header. |
| Board | Two agents' calls on one clock, by time or by steps, with the message board below. The lenses colour the calls: All, Cheating, Coordination, Collusion, Judge labels. The slider, play and step buttons move the position, and the board fills to it. On a run with `tmp_snapshots.json`, a /tmp panel shows each machine's /tmp, /var/tmp and /dev/shm at the position: each entry's owner and size, the entries added, changed or removed since the step before marked with the call that changed them, and a file's text on a click. |
| Details | The Cheating, Coordination and Collusion panels, the judge labels and a legend. |
| Run record | The end of Details: the run's settings, its labels and each agent's record. |
| Ladders | The button in the top bar: every rung and level of the three ladders, by task, what each judge label's score means, and how reliable each proved. Every definition lives here. |
