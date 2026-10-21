# integration

Code that runs CheatBench rows inside ORBIT and reads the results back. Run every module from the repository root with `.venv/bin/python -m integration.<package>.<module>`.

## task/ (what runs inside Inspect)

| file | holds |
|---|---|
| `openmath_agent.py` | the three `@task` factories (`cheatbench_openmath_agent`, `_board`, `_swarm`), the staged sandbox, the probe, the deliverables scorer |
| `board.py` | the board topology as ORBIT configuration: one shared channel, round-robin scheduling, no invocation edges |
| `swarm.py` | hidden roles on the board (worker, regulator, disregulator), the team-report instruction, role assignment by seed |
| `controls.py` | `defenses_for(control, monitor_model)`: ORBIT defense configurations for each named control, plus the trailing-turn shim |
| `provider_retry.py` | imported by the task: Gemini's 402 "credits depleted" is retried as a rate limit |
| `models.yaml` | the judge model entry CheatBench's `judge.py` reads |
| `swe_agent.py` | Lucy's plan on CheatBench SWE: `cheatbench_swe_agent` (one agent, whole issue) and `cheatbench_swe_board` (2 or 3 agents with pre-assigned parts, `jigsaw`, `rule` none, rule or wholly_own, `delivery` auto, two opening turns); prompts, parts from Lucy's `team_swe.splits` |
| `swe_env.py` | the SWE sandbox: image builds (amd64 source, native arm64 rebuild, jigsaw), one compose service per agent plus the grader, `shell` and `run_python` bound to the agent's own container with the opening-turn gate, setup with the jigsaw isolation check, the hidden-test grader |
| `swe_splits.yaml` | which of Lucy's four parts each agent owns for 2 and 3 agents, per task |

## convert/

| file | holds |
|---|---|
| `eval_to_atif.py` | one Inspect `.eval` log to `runs/<name>/`: one episode per agent, ATIF transcripts, board messages, defense evidence, deliverables, halt reason; for SWE the `swe` block in `run.json`, per-agent part, reward and opening-turn compliance, and `swe/diffs/<target>.diff` |

## checks/

| file | holds |
|---|---|
| `smoke_test.py` | six mock-model scenarios on the real sandbox (complete, message limit, time limit, board, defended, swarm) |
| `smoke_swe.py` | six mock-model scenarios for Lucy's plan on flipt-02e2 (lone, board, jigsaw, rule, comm_turns, grading); each invocation writes `runs/smoke_swe/<UTC stamp>/` |
| `rungs.py` | Rigel's cheating ladder (exposed, tried, opened, used, shipped, with a searched count beside it) over SWE run directories; writes `rungs.json` |
| `check_sweep.py` | exit 1 unless every named swarm run is complete and judged; one summary line per run |
| `check_share.py` | exit 1 unless the static bundle equals the live data, its figures match `docs/figures`, its page is exactly what the exporter builds, no endpoint is an error record, no text file is over 16 MB and no image over 15 MB, and no credential pattern or credential-like environment value appears |
| `share_browser.js` | headless Chromium pass over the served bundle (`make share-browser`): every view at 360, 400 and 1440 px with no error, no request outside the bundle and no horizontal scroll; the context note; the four theme states; a redraw after a theme change |

## report/

| file | holds |
|---|---|
| `report_pilot.py` | pilot and controls tables for the document |
| `report_swarm.py` | swarm tables for the document |
| `figures.py` | composition heatmap, role funnel, adoption curves, swimlane, controls plot |
| `summarize_runs.py` | one line per judged episode |
| `swe_summary.py` | Lucy's measures per SWE run: judge verdicts, posts about the fix and the rule, disclosure, rung, rewards; writes `swe_summary.json` per run and prints a table per arm; recomputes `rungs.json` only when it is missing |

## ui/

| file | holds |
|---|---|
| `server.py` | stdlib HTTP server over `runs/` and the eval logs; `Store` is also the data layer `report/` uses |
| `__main__.py` | `python -m integration.ui [--runs runs] [--port 8765] [--open]` |
| `export.py` | `python -m integration.ui.export`: the browser as a static bundle in a fresh directory (`index.html` complete, `artifact.html` for a publisher that adds the document skeleton, `data/`, `figures/`, `manifest.json`); exits 1 when an endpoint had to be stored as an error record; `make share` runs it and the check |
| `API.md` | the JSON contract |
| `static/` | the single-page UI: `index.html`, `app.js`, `style.css` |

## docker/judge/

The image that runs CheatBench's `judge.py` (it imports Linux-only sandbox code). Build with `docker build -t cheatbench-judge integration/docker/judge`.

`build/` (gitignored) holds the staged task built by CheatBench's `tasks/openmath_agent/build.py`, `share/`, the static bundle `make share` writes, and `swe/<task>/`: the generated Dockerfiles, compose files, the jigsaw baseline scan and the base-present reference lines.
