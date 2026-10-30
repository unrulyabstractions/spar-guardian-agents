# Entry points. Run from the repository root; everything runs through uv, which picks up .venv.
# Runs and judging go through the command line: uv run python -m src --help
# The analysis tools run in their own environment, tools/.venv (make tools-setup): uv run --project tools python -m tools
PY := uv run python
TOOLS := uv run --project tools python
RUN ?= riemann_minimal

.PHONY: help ui ui-stop smoke smoke-swe smoke-kw smoke-interview smoke-protein test test-tools lint tools-setup scout docent \
	graph transect contagion failures hawk analyse figures tables

help:  ## list the targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | sed 's/:.*##/\t/' | column -t -s "$$(printf '\t')"

ui:  ## open the results browser (starts the server when needed)
	@$(PY) -m src ui

ui-stop:  ## stop the results browser started by make ui
	@$(PY) -m src ui stop

smoke:  ## openmath mock-model scenarios on the real sandbox, no model spend
	$(PY) tests/smoke_openmath.py

smoke-swe:  ## SWE mock-model scenarios on the flipt-02e2 sandbox, no model spend
	$(PY) tests/smoke_swe.py

smoke-kw:  ## knowledge-work mock-model scenarios on the office sandbox, no model spend
	$(PY) tests/smoke_kw.py

smoke-interview:  ## subset_sum and prime_factorization mock-model scenarios, one container per agent, no model spend
	$(PY) tests/smoke_interview.py
smoke-protein:  ## protein-design mock-model scenarios on one python311 machine per agent, no model spend
	$(PY) tests/smoke_protein.py

test:  ## offline unit tests: every tests/test_*.py except the analysis tools' (make test-tools)
	$(PY) -m pytest tests --ignore-glob='tests/test_tools_*' -q

test-tools:  ## offline unit tests of the analysis tools, in their own environment
	$(TOOLS) -m pytest tests/test_tools_*.py -q

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

hawk:  ## Hawk: the scan config for one run's log, and the command to run it
	$(TOOLS) -m tools hawk $(RUN)

analyse: scout graph transect contagion failures hawk  ## every tool over one run, in the order the later ones read the earlier

figures:  ## the paper's design figures and one setup figure per task, as PNG under tmp/design/ and tmp/setup/ (gitignored)
	$(PY) -m scripts.paper.figures
	$(PY) -m scripts.paper.domain_figures

tables:  ## the paper's tables (scales, rungs against verdict fields, rungs by domain) as PNG under tmp/tables/ (gitignored)
	$(PY) -m scripts.paper.tables
