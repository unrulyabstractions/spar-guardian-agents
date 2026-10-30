"""The tests, by how they run.

- support/: what the tests share, the fakes, the mock-model helpers and the run builders.
- unit/: offline tests, one module per src module or concept (`make test`).
- tools/: offline tests of the analysis tools, in their own environment (`make test-tools`).
- smoke/: mock-model scenarios on the real sandbox, one suite per domain (`make smoke-all`).
"""
