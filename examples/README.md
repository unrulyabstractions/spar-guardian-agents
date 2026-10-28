# examples

One script per task: three neutral agents on the pull board at the small scale, the default preset. The parts of the exploit are dealt over the three agents (`access=split`), so no agent holds all of it and completing it takes the board; on knowledge work two agents share one of the two parts.

| script | task | what each agent holds |
|---|---|---|
| `openmath/riemann_minimal.py` | openmath row `riemann` | one of three slices of the complete submission's archive, in its own copy of the repository |
| `swe/flipt_minimal.py` | SWE issue `flipt-02e2`, jigsaw honeypot | one of the fix's three pieces, on its own machine |
| `knowledge_work/worktime_minimal.py` | knowledge-work row `2ea2e5b5` | two agents the review tool, which names the client's artifact; one the permission to read it |

Run one from the repository root with `OPENAI_API_KEY` set for the agents' default model and `ANTHROPIC_API_KEY` for the judge, claude-haiku-5-5 (another agents' model takes its own key, `GEMINI_API_KEY` for Gemini):

```
uv run python -m examples.openmath.riemann_minimal
```

The run lands in `runs/<name>/` like any other run (`run.json`, `episodes.jsonl`, `judge.jsonl`, `trajectories/` and `communication/`), the eval log under `logs/`, and the script then writes the run's figures under `out/<name>/`; git ignores all three: `timeline.png` (every agent's steps round by round, with the judge's verdict at the lane's end), `board.png` (the board's actions per round) and `verdicts.png` (the judge's flags per agent), each with a CSV twin; `uv run python -m src plots <run_dir>` does the same for any run. `uv run python -m src summary runs/riemann_minimal` prints the verdicts again, and the browser (`uv run python -m src ui`) lists the run.

Each script parses the shared arguments, builds its config in the open (`build_config(args.preset, args.scale, args.settings)`), names its task and calls the run. The arguments come from `examples/utils/cli.py`, which also holds the default model and the default settings:

| argument | default | what it does |
|---|---|---|
| `--model` | `gpt-6-luna` | the agents' model: an entry of `src/configuration/models.yaml` (`uv run python -m src models`) or an Inspect model string |
| `--preset` | `default` | the preset |
| `--scale` | `small` | the run's size |
| `--set KEY=VALUE` | none | a `SwarmConfig` field, applied after the default settings; repeatable |
| `--judge`, `--no-judge` | `USE_JUDGE_AS_DEFAULT` in `examples/__init__.py`, false | whether the run is judged as it finishes. Not judged: no `judge.jsonl` yet, and `uv run python scripts/rejudge.py <name>` judges the run later from its eval log, through the batch API (half price, up to a day) or with `--sync` through the normal API (minutes) |
| `--name` | the script's name | the run's name: `runs/<name>/` and `out/<name>/` |
| `--timeout` | `MODEL_TIMEOUT_S` | seconds one model request may take |

```
uv run python -m examples.swe.flipt_minimal --model gemini-3.8-flash
uv run python -m examples.knowledge_work.worktime_minimal --scale medium --set regulators=1 --name kw_regulated
uv run python -m examples.openmath.riemann_minimal --name riemann_later
uv run python scripts/rejudge.py riemann_later --sync
uv run python -m examples.openmath.riemann_minimal --judge
```

Two default settings differ from the plain small scale: the agents get an hour, and when a run is judged as it finishes (`--judge`) the judge runs synchronously, so its verdicts come back with the run. A `--set` of either overrides it.
