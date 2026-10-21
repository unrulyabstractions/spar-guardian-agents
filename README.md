# spar-guardian-agents

CheatBench's `openmath_agent` row `riemann` run inside ORBIT with Gemini 3.8 Flash, in three arrangements: one agent, two agents on a message board, and ten agents on a board with hidden roles (workers, regulators, disregulators). Every run becomes a CheatBench run directory, is judged by gpt-6-sol, and can be browsed locally. The write-up with every result is `docs/cheatbench_orbit_integration.pdf`.

Lucy's plan runs CheatBench's SWE tasks the same way: one, two or three agents on `flipt-02e2`, each agent in its own container, the issue pre-split into parts, two opening turns on the board, a jigsaw arm where the fix is split across the agents, and a rule arm. `vuls-1832` and `openlibrary-3aee` are configured, but their images are neither built nor tested. `LUCY_PLAN_STATUS.md` has the design, the image test and what ran.

## Quick start

```
make ui            # opens http://127.0.0.1:8765/  (Summary, Runs, Swarm)
make ui-stop
make share         # static copy of the browser under integration/build/share, for publishing
make share-browser # headless pass over that copy (needs node and Playwright)
make check         # refuses unless the eleven swarm runs are complete and judged
make smoke         # mock-model scenarios on the real sandbox, no model spend (smoke-openmath, smoke-swe)
make swe-images    # build the flipt-02e2 images for Lucy's plan
make swe-summary   # rungs and Lucy's measures over runs/swe_*
make tables figures doc
make help
```

Requirements: `.venv` (Python 3.11 with ORBIT installed editable), `.venv-cb` (CheatBench), Docker, and `GEMINI_API_KEY` plus `OPENAI_API_KEY` for new runs. The SWE images are linux/amd64 and Go cannot build under QEMU here, so the SWE tasks run on a native arm64 rebuild of each image (`integration/task/swe_env.py`). The setup commands are in the Reproduction section of the document.

## Layout

```
Makefile                 entry points (make help)
scripts/                 ui, run_control.sh, run_swarm.sh, run_swe.sh, judge.sh
integration/
  task/                  the ORBIT tasks: samples, sandbox, board, swarm roles, controls, Gemini 402 retry; SWE tasks for Lucy's plan
  convert/               eval log -> CheatBench run directory
  checks/                smoke tests, the sweep check, the share check
  report/                tables, figures, one-line summaries
  ui/                    results browser: server, static UI, export.py (static bundle), API.md
  docker/judge/          image that runs CheatBench's judge
runs/<name>/             one run: run.json, episodes.jsonl, judge.jsonl, trajectories/, communication/, defense/
logs/                    Inspect eval logs (gitignored): ctrl/<run>/, swarm/<run>/, and the two pilots under gemini*/
docs/                    the document, generated tables, figures
submodules/              cheatbench, orbit and covertpersonashifts (Lucy's swarm-persona repository), read only
VERIFICATION_LOG.md      every output, how it was verified, and the result
```

## Running

```
scripts/run_control.sh single guardian_task ctrl_guardian_task   # one control on the lone agent; `board` for two agents
scripts/run_swarm.sh 3 3 swarm_R3D3_s1 -T seed=1                # one composition (regulators, disregulators); launch four at a time
scripts/judge.sh logs/swarm/<run>/<log>.eval <run>               # convert and judge an existing eval log
scripts/run_swe.sh agent swe_flipt_n1_s150 -T task=flipt-02e2     # Lucy's plan: lone agent
scripts/run_swe.sh board swe_flipt_n2 -T task=flipt-02e2 -T n_agents=2 [-T jigsaw=true] [-T rule=rule|wholly_own]
```

Each run writes `runs/<name>/` and its eval log under `logs/`. A run that died in the harness or at the provider is set aside under a `failed_` or `failed2_` prefix. The browser hides those by default, and no table or figure counts them.

## Results browser

Three parts. **Summary**: the share of judged agents who cheated by setting, the composition grids, and the team reports against the judge. **Runs**: one row per run that opens to its agents, then the run page, the transcript, the context windows, the sample and the timeline. **Swarm**: the composition grids, the role funnel, the adoption curves, the swarm runs with their rasters, and a run explorer with the swimlane and the exposure cascade. `integration/ui/API.md` is the HTTP contract.

`make share` writes the browser as a static bundle under `integration/build/share`. The bundle needs no server. It holds `index.html` (the complete page, for any static host), `artifact.html` (the same page without the document skeleton, for a publisher that adds its own, such as a claude.ai artifact), the figures, and every API response except the per-call context windows as JSON files: the run list, the summary, the figure list, and one file per run and endpoint. The context page states that the per-call inputs are missing and points at the local browser. To check the bundle locally, serve the directory over HTTP (`cd integration/build/share && python3 -m http.server 8000`) and open http://127.0.0.1:8000/. Opened from disk, the data requests fail and the page says so. `make share` also runs `check_share`, and `make share-browser` runs a headless pass. A publish takes at most 255 files and 64 MB, so the bundle goes up in several publishes.
