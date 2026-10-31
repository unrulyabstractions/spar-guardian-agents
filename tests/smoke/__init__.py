"""The mock-model smoke suites, one module per domain and one for the access log, on the real sandbox and with no model
spend.

    uv run python -m tests.smoke.<domain> [--only a,b]      # one suite
    make smoke-all                                          # every suite at once

common.py holds what the suites share and all_suites.py runs them together.
"""
