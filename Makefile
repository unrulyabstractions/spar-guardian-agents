# Entry points. Run from the repository root; everything runs through uv, which picks up .venv.
# Runs and judging go through the command line: uv run python -m src --help
# The analysis tools run in their own environment, tools/.venv (make tools-setup): uv run --project tools python -m tools
PY := uv run python
TOOLS := uv run --project tools python
RUN ?= riemann_minimal
SAMPLES ?= 15

.PHONY: help ui ui-stop smoke smoke-swe smoke-kw smoke-interview smoke-protein smoke-creative smoke-chess smoke-access \
	smoke-load smoke-all test test-tools lint \
	tools-setup scout docent graph transect contagion failures hawk analyse figures tables

help:  ## list the targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | sed 's/:.*##/\t/' | column -t -s "$$(printf '\t')"

ui:  ## open the results browser (starts the server when needed)
	@$(PY) -m src ui

ui-stop:  ## stop the results browser started by make ui
	@$(PY) -m src ui stop

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

smoke-all:  ## every smoke suite at once: one log per suite under logs/smoke-all/, a summary, nonzero on any failure
	$(PY) -m tests.smoke.all_suites

test:  ## offline unit tests: tests/unit
	$(PY) -m pytest tests/unit -q

test-tools:  ## offline unit tests of the analysis tools, in their own environment: tests/tools
	$(TOOLS) -m pytest tests/tools -q

lint:  ## ruff over src, tests, examples, scripts and tools (ruff.toml: 120 columns, sorted imports)
	uvx ruff check src tests examples scripts tools

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

figures:  ## the paper's design figures and one setup figure per task, as PNG under tmp/design/ and tmp/setup/ (gitignored)
	$(PY) -m scripts.paper.figures
	$(PY) -m scripts.paper.domain_figures

tables:  ## the paper's tables (scales, rungs against verdict fields, rungs and collaboration by domain) as PNG under tmp/tables/ (gitignored)
	$(PY) -m scripts.paper.tables
