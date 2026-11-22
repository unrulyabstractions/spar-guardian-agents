# Seed packs

A seed pack is what a run's environment holds before round 1 beyond the task: board posts and files left by a
"previous team". A pack may point, tempt, normalise, rationalise or urge the team to work together. It never holds a
piece of the exploit, so a seeded team still needs two agents' items to cheat.

## Format

One folder per pack, `seeds/<task>/<pack>/`. `<task>` is the benchmark's key (`swe`, `protein_design`,
`creative_writing`). A run names the pack `<task>/<pack>`.

```
seeds/swe/collaborate/
  pack.yaml
  files/tmp/previous_team_plan.md      planted at /tmp/previous_team_plan.md of every machine, root's, mode 0644
```

A file under `files/home/<name>` goes to `~/<name>` of every agent, the agent's own. The file's bytes are planted as
they are. `pack.yaml` takes these keys and no others:

| key | what it holds |
|---|---|
| `about` | one sentence on what the pack holds |
| `kinds` | among `pointer`, `normalise`, `rationalise`, `pressure`, `pool`, `collaborate` |
| `harvest` | the real posts it was written from: `<study>/<run>#<board sequence>`, or an id of the 2026-11-22 harvest such as `swe-01` |
| `rows` | the task rows its text fits; every row when absent |
| `posts` | the board posts, oldest first: `{author: k, text: "..."}`, authors numbered 1, 2, ... |

Each run draws the previous team's IDs off its roster. `{author<k>}` in a post or a file becomes the k-th author's ID.

## Add a pack

1. Copy a pack folder, such as `seeds/swe/collaborate/`, to `seeds/<task>/<new_pack>/`.
2. Write its posts from real posts of past runs (`runs/<study>/<run>/communication/*.json`). Take out agent IDs,
   commit ids and every piece of the exploit. List the sources under `harvest`.
3. Put its files under `files/tmp/` or `files/home/`.
4. Check it.

## Check a pack

```
make seeds-check
```

It loads every pack and refuses a malformed one with what to fix. It then runs each task's collaboration tests on
every pack (`tests/unit/test_collaboration_*.py`), which fail when a pack holds a secret, a line of the fix, a planted
design or a line of the reference. A task with no collaboration test takes no pack.

## Run with a pack

```
uv run python -m examples.swe.flipt_minimal --model gpt-6-luna-openrouter --no-judge --set seed_pack=swe/collaborate
```

run.json `seed` records the pack, the previous team's IDs and every post and file, and `launch` records the command.
A study of many cells runs from a plan in `studies/`, one run per target, pack and seed:

```
uv run python -m scripts.run_study studies/ian_seed_prototyping_0.yaml --dry-run
```

Without `--dry-run` it needs `--yes`, because the runs make paid model calls.
