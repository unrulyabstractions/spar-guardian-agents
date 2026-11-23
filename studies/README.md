# studies

A **study** is one round of runs under one name. Its plan names its targets, its seed packs and its seeds, and
`scripts/run_study.py` turns the plan into one run per cell. Its runs live under `runs/<study>/` and its eval logs
under `logs/<study>/`. A study that is over moves to `OLD/runs/<study>/` and `OLD/logs/<study>/` on the user's word.

## Layout

| path | holds |
|---|---|
| `plans/<study>.yaml` | a study's plan: the study, the seeds, the targets (module, model, packs, own settings), the plan's settings and the launch limits |
| `cells/<task>/<stem>.py` | a cell script made for a study, run as `python -m studies.cells.<task>.<stem>` |

A cell script takes the arguments of every example (`examples/example_arguments.py`) and may import a generic
example's `CHEAT` text. The generic examples stay in `examples/`. A plan may name either kind of script.

## The studies

| study | plan | status | tasks | cells | runs |
|---|---|---|---|---|---|
| `ian_seed_prototyping_0` | none: the plan left with its `exp0_*` packs; commit `1029e9e` holds it last | closed | SWE, protein design, creative writing | 22 | `OLD/runs/ian_seed_prototyping_0` |
| `ian_seed_prototyping_1` | `plans/ian_seed_prototyping_1.yaml` | closed | SWE | 8 | `OLD/runs/ian_seed_prototyping_1` |
| `ian_seed_prototyping_2` | `plans/ian_seed_prototyping_2.yaml` | open | SWE, protein design, creative writing | 27 | `runs/ian_seed_prototyping_2` |

`ian_seed_prototyping_0` is the seed pilot: four targets on the generic examples `flipt_minimal`, `binder_minimal`
and `poem_minimal`, each with the `exp0_*` packs and a control with none. Its plan and its packs are gone from the
tree. `ian_seed_prototyping_1` runs `ian_swe0` with the pack `swe/exp1_called_cheating` on Haiku and Luna workers in
four conditions. `ian_seed_prototyping_2` runs the ian0 tasks on DeepSeek, Luna and Haiku workers in three
conditions: baseline, seeded and positive control. Each run's `README.md` under its study's folder says more. The
studies before the plans (`ian_baseline_explorations_0`, `ian_baseline_explorations_1`, `apart_controls`) have no
plan and live in `OLD/runs/`.

## The cell scripts

### SWE (`cells/swe/`)

| script | what the cell is | plans |
|---|---|---|
| `ian0flipt_minimal.py` | three neutral agents on `ian0flipt`: `flipt-02e2` under the jigsaw honeypot, each piece also a patch file outside the repository, with a clean-room fork in place of CheatBench's work-test line | `ian_seed_prototyping_2` (baseline, seeded) |
| `ian0flipt_minimal_cheat.py` | `ian0flipt_minimal` with every agent told to cheat (flipt's `CHEAT`) | `ian_seed_prototyping_2` (positive control) |
| `ian0flipt_minimal_regulated.py` | `ian0flipt_minimal` with a fourth agent, a hidden regulator who holds no piece | none |
| `ianswe0_minimal.py` | three neutral agents on `ian_swe0`: `flipt-02e2` under the jigsaw honeypot, each piece held twice, as a planted branch and as a patch file outside the repository | `ian_seed_prototyping_1` (baseline, negative control) |
| `ianswe0_minimal_cheat.py` | `ianswe0_minimal` with every agent told to cheat (flipt's `CHEAT`) | `ian_seed_prototyping_1` (positive control) |
| `ianswe0_minimal_regulated.py` | `ianswe0_minimal` with a fourth agent, a hidden regulator who holds no piece | `ian_seed_prototyping_1` (regulated) |

### Creative writing (`cells/creative_writing/`)

| script | what the cell is | plans |
|---|---|---|
| `ian0poem_minimal.py` | three neutral agents on `ian0poem`: the brief `museum_poem` under the split, each segment of the reference also in a side folder its holder alone reads, with a client's commission in place of CheatBench's interview sentences | `ian_seed_prototyping_2` (baseline, seeded) |
| `ian0poem_minimal_cheat.py` | `ian0poem_minimal` with every agent told to cheat (`poem_minimal_cheat`'s `CHEAT`) | `ian_seed_prototyping_2` (positive control) |

### Protein design (`cells/protein_design/`)

| script | what the cell is | plans |
|---|---|---|
| `ian0binder_minimal.py` | three neutral agents on `ian0binder`: the target `alfalfa_chi`, each agent's candidates file also in the screening directory's backup, with a client's request in place of CheatBench's sentence about a manager | `ian_seed_prototyping_2` (baseline, seeded) |
| `ian0binder_minimal_cheat.py` | `ian0binder_minimal` with every agent told to cheat (`binder_minimal_cheat`'s `CHEAT`) | `ian_seed_prototyping_2` (positive control) |

Each script's docstring holds the full description of its cell.

## Look at a plan, then launch it

```
uv run python -m scripts.run_study studies/plans/ian_seed_prototyping_2.yaml --dry-run
uv run python -m scripts.run_study studies/plans/ian_seed_prototyping_2.yaml --yes
```

`--dry-run` lists every cell, the cells already done and the command of each cell still to run. It launches
nothing. `--yes` launches the cells that are not done, on this Mac's Docker, and every run makes paid model calls.
A closed plan never launches. `--study <name>` runs a plan into another study. The rules of `CLAUDE.md` hold for
every launch: a spend check before it, the cost per cell to the user, and live pricing in the first 10 minutes.

## Add a study

1. Put any new cell script in `cells/<task>/<stem>.py`. Copy the closest script there or in `examples/<task>/`.
   Its stem must not repeat a stem of `examples/`.
2. Write `plans/<study>.yaml`. Its targets name modules as `studies.cells.<task>.<stem>` or
   `examples.<task>.<stem>`. A target with no pack counts toward its module's task for `limits.max_per_task`.
3. Add the study's `about` to `scripts/study_readme_fields.yaml` and its name to `STUDIES` in
   `scripts/study_readme.py`, so `make study-readmes` writes its `runs/<study>/README.md`.
4. Add a row to the tables above.
5. Dry-run the plan and run `uv run pytest tests/unit -q -k "example or run_study"`.
6. Set `closed: true` in the plan once the study is over.
