# Entry points. Run from the repository root; everything runs through uv, which picks up .venv.
# Runs and judging go through the command line: uv run python -m src --help
# The analysis tools run in their own environment, tools/.venv (make tools-setup): uv run --project tools python -m tools
PY := uv run python
TOOLS := uv run --project tools python
RUN ?= riemann_minimal
SAMPLES ?= 15

.PHONY: help ui ui-stop transcripts smoke smoke-swe smoke-kw smoke-interview smoke-protein smoke-creative smoke-chess \
	smoke-access smoke-load smoke-all smoke-replay test test-tools lint \
	tools-setup scout docent graph transect contagion failures hawk analyse figures tables \
	results-tables paper-figures paper-sources paper-check paper

help:  ## list the targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | sed 's/:.*##/\t/' | column -t -s "$$(printf '\t')"

ui:  ## open the results browser (starts the server when needed)
	@$(PY) -m src ui

ui-stop:  ## stop the results browser started by make ui
	@$(PY) -m src ui stop

transcripts:  ## serve the transcript viewer on port 8770 in the foreground (REBUILD=1 rebuilds its index)
	@$(PY) -m src.ui.transcript_viewer $(if $(REBUILD),--rebuild)

smoke:  ## openmath mock-model scenarios on the real sandbox, no model spend
	$(PY) -m tests.smoke.openmath

smoke-swe:  ## SWE mock-model scenarios on the flipt-02e2 sandbox, no model spend
	$(PY) -m tests.smoke.swe

smoke-kw:  ## knowledge-work mock-model scenarios on the office sandbox, no model spend
	$(PY) -m tests.smoke.knowledge

smoke-interview:  ## subset_sum and prime_factorization mock-model scenarios, one container per agent, no model spend
	$(PY) -m tests.smoke.interview

smoke-protein:  ## protein-design mock-model scenarios on one python311 machine per agent, no model spend
	$(PY) -m tests.smoke.protein

smoke-creative:  ## creative-writing mock-model scenarios on one container with the review sidecar, no model spend
	$(PY) -m tests.smoke.creative

smoke-chess:  ## chess mock-model scenarios: the game and Stockfish on the host, one python311 container, no model spend
	$(PY) -m tests.smoke.chess

smoke-access:  ## the access log's scenarios on the real sandbox, on ordered and on drawn agent IDs, no model spend
	$(PY) -m tests.smoke.access_logging

smoke-load:  ## SAMPLES runs at once on one Docker, each with its own sidecar, as a box runs them, no model spend
	$(PY) -m tests.smoke.access_load --samples $(SAMPLES)

smoke-replay:  ## the replay of a tiny synthetic SWE run recorded in a real sandbox, under build/replay_smoke/<stamp>/; not in smoke-all
	$(PY) -m tests.smoke.replay_swe

smoke-all:  ## every smoke suite at once: one log per suite under logs/study_smoke/smoke_all/<stamp>/, a summary, nonzero on any failure
	$(PY) -m tests.smoke.all_suites

test:  ## offline unit tests: tests/unit
	$(PY) -m pytest tests/unit -q

test-tools:  ## offline unit tests of the analysis tools, in their own environment: tests/tools
	$(TOOLS) -m pytest tests/tools -q

lint:  ## ruff over src, tests, examples, elicitation, scripts and tools (ruff.toml: 120 columns, sorted imports)
	uvx ruff check src tests examples elicitation scripts tools

.PHONY: hf-upload
hf-upload:  ## upload STUDY's runs and eval logs to the Hugging Face dataset spar-swarm (RUN="<run> ..." for some, DRY_RUN=1 to list)
	@test -n "$(STUDY)" || { echo "make hf-upload: give STUDY=<study>, e.g. make hf-upload STUDY=study_exploration_1"; exit 2; }
	$(PY) scripts/upload_runs_to_hf.py "$(STUDY)" $(if $(findstring command line,$(origin RUN)),--run "$(RUN)") $(if $(DRY_RUN),--dry-run)

.PHONY: hf-pull
hf-pull:  ## download STUDY's runs and eval logs from the dataset spar-swarm into runs/ and logs/ (RUN="<run> ..." for some, DRY_RUN=1 to list, FORCE=1 to replace differing files, DEST=<dir> for another root)
	@test -n "$(STUDY)" || { echo "make hf-pull: give STUDY=<study>, e.g. make hf-pull STUDY=study_exploration_1 RUN=<run>"; exit 2; }
	$(PY) scripts/pull_runs_from_hf.py "$(STUDY)" $(if $(findstring command line,$(origin RUN)),--run "$(RUN)") $(if $(DRY_RUN),--dry-run) $(if $(FORCE),--force) $(if $(DEST),--dest "$(DEST)")

.PHONY: replay
replay:  ## replay STUDY's runs from their recorded calls in Docker (no model call) and put each faithful replay's steps beside its run (RUN="<run> ..." for some, JOBS=2)
	@test -n "$(STUDY)" || { echo "make replay: give STUDY=<study>, e.g. make replay STUDY=study_exploration_1"; exit 2; }
	$(PY) scripts/replay_study.py "$(STUDY)" $(if $(findstring command line,$(origin RUN)),$(foreach r,$(RUN),--run "$(r)")) $(if $(JOBS),--jobs $(JOBS))

.PHONY: setup
setup:  ## the environment: .venv (Python 3.11) with requirements.txt's pinned packages and ORBIT from its submodule
	uv venv --python 3.11 .venv
	uv pip install --python .venv/bin/python --no-deps -r requirements.txt
	uv pip install --python .venv/bin/python --no-deps -e submodules/orbit

tools-setup:  ## the analysis environment: tools/.venv (Python 3.12) and MAST's taxonomy texts
	uv sync --project tools --python 3.12
	$(TOOLS) -m tools.failures.fetch_mast_taxonomy

scout:  ## Inspect Scout: the cheating scanners over one run's agents (RUN=<name>)
	$(TOOLS) -m tools scout $(RUN)

docent:  ## Docent: ingest one run's agents as agent runs (needs DOCENT_API_KEY; --dry-run without)
	$(TOOLS) -m tools docent $(RUN)

graph:  ## NetworkX: who read what before cheating; graph.png and graph.gexf for Gephi
	$(TOOLS) -m tools graph $(RUN)

transect:  ## Transect: the step-by-step report of one run
	$(TOOLS) -m tools transect $(RUN)

contagion:  ## NDlib and EoN: how dishonest and honest behaviour spread, against standard contagion models
	$(TOOLS) -m tools contagion $(RUN)

failures:  ## MAST and Who&When: how the swarm failed and which agent was responsible
	$(TOOLS) -m tools failures $(RUN)

hawk:  ## Hawk: the scan config for one run's transcripts, and the command to run it
	$(TOOLS) -m tools hawk $(RUN)

analyse: scout graph transect contagion failures hawk  ## every tool but docent over one run, in order; scout, transect and failures call a paid model

figures:  ## the paper's design figures and one setup figure per task, as PNG under tmp/figures/method/design/ and tmp/figures/method/setup/ (gitignored)
	$(PY) -m scripts.paper.figures
	$(PY) -m scripts.paper.domain_figures

tables:  ## the paper's method table (the Cheating ladder by task) as PNG under tmp/tables/method/ (gitignored)
	$(PY) -m scripts.paper.tables

results-tables:  ## the paper's results tables from the runs on disk, as PNG under tmp/tables/results/ (gitignored)
	$(PY) -m scripts.paper.control_tables
	$(PY) -m scripts.paper.protein_regulator_table
	$(PY) -m scripts.paper.swe_regulator_table
	$(PY) -m scripts.paper.creative_regulator_table
	$(PY) -m scripts.paper.disregulator_tables
	$(PY) -m scripts.paper.baseline_tables
	$(PY) -m scripts.paper.solo_controls_table
	$(PY) -m scripts.paper.cheating_table
	$(PY) -m scripts.paper.coordination_table
	$(PY) -m scripts.paper.collusion_table
	$(PY) -m scripts.paper.judge_tables
	$(PY) -m scripts.paper.label_tables

paper-figures:  ## the paper's Figures 1 to 5 from Lucy's summaries, the run index and the code, as PNG under tmp/figures/paper/ (gitignored)
	$(PY) -m scripts.paper.fig_scripted_teammates
	$(PY) -m scripts.paper.fig_roles_access
	$(PY) -m scripts.paper.fig_jigsaw
	$(PY) -m scripts.paper.fig_multidomain
	$(PY) -m scripts.paper.fig_deep_dive

paper-sources:  ## paper/generated/*.tex from the code, and paper/figures/ copied from tmp/ (scripts/paper/paper_sources.py)
	$(PY) -m scripts.paper.paper_sources

paper-check:  ## exit 1 when paper/ disagrees with the code or with tmp/; writes nothing
	$(PY) -m scripts.paper.paper_sources --check

paper: figures tables results-tables paper-figures paper-sources  ## regenerate every source, then build paper/build/main.pdf with latexmk
	cd paper && latexmk -pdf -interaction=nonstopmode -halt-on-error -outdir=build main.tex
