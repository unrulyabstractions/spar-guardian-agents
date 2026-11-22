# Seed packs

A seed pack is what a run's environment holds before round 1 beyond the task: board posts and files left by a
"previous team". A pack may point, tempt, normalise, rationalise or urge the team to work together. It never holds a
piece of the exploit, so a seeded team still needs two agents' items to cheat.

## Format

One folder per pack, `seeds/<task>/<pack>/`. `<task>` is the benchmark's key (`swe`, `protein_design`,
`creative_writing`). A run names the pack `<task>/<pack>`.

```
seeds/swe/exp0_pooled_and_passed/
  README.md                         one paragraph on what the pack does, then `Kinds: pool, pointer, normalise.`
  pack.yaml                         kinds: [pool, pointer, normalise] and rows: [flipt-02e2]
  board/01.md                       the first post: `---`, `author: 1`, `---`, then its text
  board/02.md
  board/03.md
  files/tmp/previous_team_notes.md  planted at /tmp/previous_team_notes.md of every machine, root's, mode 0644
  harvest/<study>__<run>__25.md     a real post the pack was written from, verbatim, for people
```

`README.md`'s first paragraph is the pack's `about`, which run.json records. Its second paragraph names the pack's
kinds, the same as `pack.yaml`'s, among `pointer`, `normalise`, `rationalise`, `pressure`, `pool` and `collaborate`.
`pack.yaml` holds `kinds` and, when the text fits only some of the task's rows, `rows`.

The posts stand on the board in the order of their numbers: `01.md`, `02.md`, and so on, with no gap. Each starts with
a front matter naming its author, then holds the post's text exactly as posted. Authors are numbered 1, 2, ... with no
gap. Each run draws the previous team's IDs off its roster, and `{author<k>}` in a post or a file becomes the k-th
author's ID. A pack of files alone has no `board/`.

A file under `files/tmp/<name>` goes to `/tmp/<name>` of every machine. A file under `files/home/<name>` goes to
`~/<name>` of every agent, the agent's own. Both are planted byte for byte.

`harvest/` holds the real posts and notes of past runs that the pack was written from, one file each, copied verbatim
with their source, the sender's role and the round in a front matter. Nothing reads it, and nothing in the pack names
it.

## Add a pack

1. Copy a pack folder, such as `seeds/swe/exp0_pooled_and_passed/`, to `seeds/<task>/<new_pack>/`.
2. Replace `harvest/` with the real posts you write from (`runs/<study>/<run>/communication/*.json`, or under
   `OLD/runs/` for an archived study).
3. Write the posts in `board/` and the files in `files/`. Take out agent IDs, commit ids and every piece of the
   exploit.
4. Rewrite `README.md` and set the kinds in both `README.md` and `pack.yaml`.
5. Check it.

## Check a pack

```
make seeds-check
```

It loads every pack and refuses a malformed one with what to fix. It then runs each task's collaboration tests on
every pack (`tests/unit/test_collaboration_*.py`), which fail when a pack holds a secret, a line of the fix, a planted
design or a line of the reference. A task with no collaboration test takes no pack.

## Run with a pack

```
uv run python -m examples.swe.flipt_minimal --model gpt-6-luna-openrouter --no-judge --set seed_pack=swe/exp0_collaborate
```

run.json `seed` records the pack, the previous team's IDs and every post and file, and `launch` records the command.
A study of many cells runs from a plan in `studies/`, one run per target, pack and seed:

```
uv run python -m scripts.run_study studies/ian_seed_prototyping_0.yaml --dry-run
```

Without `--dry-run` it needs `--yes`, because the runs make paid model calls.
