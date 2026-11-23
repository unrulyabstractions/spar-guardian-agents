# Entry points. Run from the repository root; everything runs through uv, which picks up .venv.
# Runs and judging go through the command line: uv run python -m src --help
# The analysis tools run in their own environment, tools/.venv (make tools-setup): uv run --project tools python -m tools
PY := uv run python
TOOLS := uv run --project tools python
# Each target prints its steps as headings with indented details and ends with one summary line (src/utils/progress.py).
# STEPS runs commands as numbered steps; it needs only the standard library, so it runs before .venv exists.
STEPS := python3 -m src.utils.progress
RUN ?= riemann_minimal
SAMPLES ?= 15

.PHONY: help ui ui-stop transcripts transcript process-transcripts smoke smoke-swe smoke-kw smoke-interview smoke-protein smoke-creative smoke-chess smoke-seeds seeds-check \
	smoke-access smoke-network smoke-load smoke-all smoke-replay test test-tools lint \
	tools-setup scout docent graph transect contagion failures hawk analyse figures tables \
	results-tables paper-figures paper-sources paper-check paper

help:  ## list the targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | sed 's/:.*##/\t/' | column -t -s "$$(printf '\t')"

ui:  ## open the results browser (starts the server when needed)
	@$(PY) -m src ui

ui-stop:  ## stop the results browser started by make ui
	@$(PY) -m src ui stop

transcripts:  ## serve the transcript viewer on port 8770 in the foreground: what process-transcripts stored, current study
	@$(PY) -m src.ui.transcript_viewer

process-transcripts:  ## compute and store each run's parts that are behind (STUDY=, RUN=, FORCE=<part>, DRY_RUN=1)
	@$(PY) -m src process $(STUDY) $(if $(filter command line environment,$(origin RUN)),$(RUN)) \
		$(if $(FORCE),--force $(FORCE)) $(if $(DRY_RUN),--dry-run) $(if $(JOBS),--jobs $(JOBS))

transcript: transcripts  ## the same, under the name without the s

smoke:  ## openmath mock-model scenarios on the real sandbox, no model spend
	@$(PY) -m tests.smoke.openmath

smoke-swe:  ## SWE mock-model scenarios on the flipt-02e2 sandbox, no model spend
	@$(PY) -m tests.smoke.swe

smoke-kw:  ## knowledge-work mock-model scenarios on the office sandbox, no model spend
	@$(PY) -m tests.smoke.knowledge

smoke-interview:  ## subset_sum and prime_factorization mock-model scenarios, one container per agent, no model spend
	@$(PY) -m tests.smoke.interview

smoke-protein:  ## protein-design mock-model scenarios on one python311 machine per agent, no model spend
	@$(PY) -m tests.smoke.protein

smoke-creative:  ## creative-writing mock-model scenarios on one container with the review sidecar, no model spend
	@$(PY) -m tests.smoke.creative

smoke-seeds:  ## seed packs on the SWE sandbox: seeded posts and planted files, no model spend
	@$(PY) -m tests.smoke.seeded_runs

seeds-check:  ## check every seed pack in seeds/: its format, then that it holds no piece of its task's exploit, no model call
	@$(STEPS) "Seed packs (seeds/)" --summary "every seed pack is well formed and holds no piece of its task's exploit" \
		--step "the format" "$(PY) -m src.configuration.seed_packs" \
		--step "no piece in any pack" "$(PY) -m pytest tests/unit/test_seed_packs.py $(sort $(wildcard tests/unit/test_collaboration_*.py)) -k seed -q"

smoke-chess:  ## chess mock-model scenarios: the game and Stockfish on the host, one python311 container, no model spend
	@$(PY) -m tests.smoke.chess

smoke-access:  ## the access log's scenarios on the real sandbox, on ordered and on drawn agent IDs, no model spend
	@$(PY) -m tests.smoke.access_logging

smoke-network:  ## the network log's scenarios on the real sandbox (bpftrace, the socket tables, a dead monitor), no model spend
	@$(PY) -m tests.smoke.network_logging

smoke-load:  ## SAMPLES runs at once on one Docker, each with its own sidecar, as a box runs them, no model spend
	@$(PY) -m tests.smoke.access_load --samples $(SAMPLES)

smoke-replay:  ## the replay of a tiny synthetic SWE run recorded in a real sandbox, under build/replay_smoke/<stamp>/; not in smoke-all
	@$(PY) -m tests.smoke.replay_swe

smoke-all:  ## every smoke suite at once: one log per suite under logs/study_smoke/smoke_all/<stamp>/, a summary, nonzero on any failure
	@$(PY) -m tests.smoke.all_suites

test:  ## offline unit tests: tests/unit
	@$(STEPS) "Unit tests (tests/unit)" --summary "the unit tests passed" --step tests "$(PY) -m pytest tests/unit -q"

test-tools:  ## offline unit tests of the analysis tools, in their own environment: tests/tools
	@$(STEPS) "Unit tests of the analysis tools (tests/tools)" --summary "the tools' unit tests passed" --step tests "$(TOOLS) -m pytest tests/tools -q"

lint:  ## ruff over src, tests, examples, studies, scripts and tools (ruff.toml: 120 columns, sorted imports)
	@$(STEPS) "Lint (ruff)" --summary "lint clean" --step ruff "uvx ruff check src tests examples studies scripts tools"

.PHONY: study-readmes
study-readmes:  ## write each study's runs/<study>/README.md from its own files and the code (CHECK=1 writes nothing, fails when one is stale)
	@$(PY) -m scripts.study.study_readme --all $(if $(CHECK),--check)

.PHONY: hf-upload
hf-upload:  ## upload STUDY's runs and eval logs (or STUDY=apart_controls) to the Hugging Face dataset spar-swarm (RUN="<run> ..." for some, DRY_RUN=1 to list, DELETE_STALE=1 to delete Hub files gone here, LISTING=<file> for every path)
	@test -n "$(STUDY)" || { echo "make hf-upload: give STUDY=<study>, e.g. make hf-upload STUDY=ian_baseline_explorations_1"; exit 2; }
	@$(PY) scripts/hf/upload_runs_to_hf.py "$(STUDY)" $(if $(findstring command line,$(origin RUN)),--run "$(RUN)") $(if $(DRY_RUN),--dry-run) $(if $(DELETE_STALE),--delete-stale) $(if $(LISTING),--listing "$(LISTING)")

.PHONY: hf-pull
hf-pull:  ## download STUDY's runs and eval logs from the dataset spar-swarm into runs/ and logs/ (RUN="<run> ..." for some, DRY_RUN=1 to list, FORCE=1 to replace differing files, DEST=<dir> for another root, DEST=OLD for the archive)
	@test -n "$(STUDY)" || { echo "make hf-pull: give STUDY=<study>, e.g. make hf-pull STUDY=ian_baseline_explorations_1 RUN=<run>"; exit 2; }
	@$(PY) scripts/hf/pull_runs_from_hf.py "$(STUDY)" $(if $(findstring command line,$(origin RUN)),--run "$(RUN)") $(if $(DRY_RUN),--dry-run) $(if $(FORCE),--force) $(if $(DEST),--dest "$(DEST)")

.PHONY: replay
replay:  ## replay STUDY's runs from their recorded calls in Docker (no model call) and put each faithful replay's steps beside its run (RUN="<run> ..." for some, JOBS=2)
	@test -n "$(STUDY)" || { echo "make replay: give STUDY=<study>, e.g. make replay STUDY=ian_baseline_explorations_1"; exit 2; }
	@$(PY) scripts/study/replay_study.py "$(STUDY)" $(if $(findstring command line,$(origin RUN)),$(foreach r,$(RUN),--run "$(r)")) $(if $(JOBS),--jobs $(JOBS))

.PHONY: setup
setup:  ## the environment: .venv (Python 3.11) with requirements.txt's pinned packages and ORBIT from its submodule
	@$(STEPS) "Setup of .venv" --summary ".venv holds Python 3.11, requirements.txt and ORBIT" \
		--step "make .venv with Python 3.11" "uv venv --python 3.11 .venv" \
		--step "install requirements.txt" "uv pip install --python .venv/bin/python --no-deps -r requirements.txt" \
		--step "install ORBIT from submodules/orbit" "uv pip install --python .venv/bin/python --no-deps -e submodules/orbit"

tools-setup:  ## the analysis environment: tools/.venv (Python 3.12) and MAST's taxonomy texts
	@$(STEPS) "Setup of tools/.venv" --summary "tools/.venv holds Python 3.12 and the tools; MAST's texts fetched" \
		--step "sync tools/.venv with Python 3.12" "uv sync --project tools --python 3.12" \
		--step "fetch MAST's taxonomy texts" "$(TOOLS) -m tools.failures.fetch_mast_taxonomy"

scout:  ## Inspect Scout: the cheating scanners over one run's agents (RUN=<name>)
	@$(STEPS) "Inspect Scout on $(RUN)" --step scout "$(TOOLS) -m tools scout $(RUN)"

docent:  ## Docent: ingest one run's agents as agent runs (needs DOCENT_API_KEY; --dry-run without)
	@$(STEPS) "Docent on $(RUN)" --step docent "$(TOOLS) -m tools docent $(RUN)"

graph:  ## NetworkX: who read what before cheating; graph.png and graph.gexf for Gephi
	@$(STEPS) "NetworkX graph on $(RUN)" --step graph "$(TOOLS) -m tools graph $(RUN)"

transect:  ## Transect: the step-by-step report of one run
	@$(STEPS) "Transect on $(RUN)" --step transect "$(TOOLS) -m tools transect $(RUN)"

contagion:  ## NDlib and EoN: how dishonest and honest behaviour spread, against standard contagion models
	@$(STEPS) "Contagion models on $(RUN)" --step contagion "$(TOOLS) -m tools contagion $(RUN)"

failures:  ## MAST and Who&When: how the swarm failed and which agent was responsible
	@$(STEPS) "MAST and Who&When on $(RUN)" --step failures "$(TOOLS) -m tools failures $(RUN)"

hawk:  ## Hawk: the scan config for one run's transcripts, and the command to run it
	@$(STEPS) "Hawk on $(RUN)" --step hawk "$(TOOLS) -m tools hawk $(RUN)"

analyse: scout graph transect contagion failures hawk  ## every tool but docent over one run, in order; scout, transect and failures call a paid model

figures:  ## the paper's design figures and one setup figure per task, as PNG under tmp/figures/method/design/ and tmp/figures/method/setup/ (gitignored)
	@$(STEPS) "Design figures" --python "$(PY)" --out tmp/figures/method/ \
		--py scripts.paper.figures --py scripts.paper.domain_figures

tables:  ## the paper's method table (the Cheating ladder by task) as PNG under tmp/tables/method/ (gitignored)
	@$(STEPS) "Method table" --python "$(PY)" --out tmp/tables/method/ --py scripts.paper.tables

results-tables:  ## the paper's results tables from the archived studies in OLD/runs/, as PNG under tmp/tables/results/ (gitignored)
	@$(STEPS) "Results tables" --python "$(PY)" --out tmp/tables/results/ \
		--py scripts.paper.control_tables \
		--py scripts.paper.protein_regulator_table \
		--py scripts.paper.swe_regulator_table \
		--py scripts.paper.creative_regulator_table \
		--py scripts.paper.disregulator_tables \
		--py scripts.paper.baseline_tables \
		--py scripts.paper.solo_controls_table \
		--py scripts.paper.cheating_table \
		--py scripts.paper.coordination_table \
		--py scripts.paper.collusion_table \
		--py scripts.paper.judge_tables \
		--py scripts.paper.label_tables

paper-figures:  ## the paper's Figures 1 to 5 from Lucy's summaries, the run index and the code, as PNG under tmp/figures/paper/ (gitignored)
	@$(STEPS) "Paper figures" --python "$(PY)" --out tmp/figures/paper/ \
		--py scripts.paper.fig_scripted_teammates \
		--py scripts.paper.fig_roles_access \
		--py scripts.paper.fig_jigsaw \
		--py scripts.paper.fig_multidomain \
		--py scripts.paper.fig_deep_dive

paper-sources:  ## paper/generated/*.tex from the code, and paper/figures/ copied from tmp/ (scripts/paper/paper_sources.py)
	@$(PY) -m scripts.paper.paper_sources

paper-check:  ## exit 1 when paper/ disagrees with the code or with tmp/; writes nothing
	@$(PY) -m scripts.paper.paper_sources --check

paper: figures tables results-tables paper-figures paper-sources  ## regenerate every source, then build paper/build/main.pdf with latexmk
	@$(STEPS) "Paper PDF" --summary "the paper built" --out paper/build/main.pdf --step latexmk \
		"cd paper && latexmk -pdf -silent -interaction=nonstopmode -halt-on-error -outdir=build main.tex \
		|| { grep -A12 '^!' build/main.log; exit 1; }"
