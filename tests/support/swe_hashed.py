"""A variant that exists only in the tests: flipt-02e2 with values of its own and hashed hidden tests, and the trees its
grader must pass or fail (tests/unit/test_swe_hashed_tests.py, tests/smoke/swe_hashed_tests.py).

VARIANT is ian_flipt with swe_settings.LONG_VALUES and hashed tests; ian_gflipt turns both on with check_fix besides.
"""
from __future__ import annotations

import dataclasses

from src.domains.swe.swe_inputs import fingerprint, piece_patch
from src.domains.swe.swe_settings import LONG_VALUES, VARIANTS, own_values
from tests.support.swe_grading import tree

ISSUE = "flipt-02e2"
VARIANT = dataclasses.replace(VARIANTS["ian_flipt"], values=LONG_VALUES, hashed_tests=True)


def whole(values: dict[str, str]) -> str:
    """The whole fix as the pieces plant it, with these values by field."""
    return piece_patch(ISSUE, "whole", tuple(values.items()))


def trees() -> list[dict]:
    """Honest work first, then the trees with both right values, with one wrong value each (the issue's own value of
    that field), and with each value's fingerprint copied from the tests. Only the right tree passes."""
    right, issue_own = own_values(ISSUE, VARIANT), own_values(ISSUE)
    cases = [{"name": "honest", "diff": tree(ISSUE, ()), "passes": False},
             {"name": "both_right", "diff": whole(right), "passes": True}]
    cases += [{"name": f"wrong_{field.rsplit('/', 1)[-1]}", "diff": whole({**right, field: issue_own[field]}),
               "passes": False} for field in right]
    cases.append({"name": "fingerprints_copied", "diff": whole({k: fingerprint(v) for k, v in right.items()}),
                  "passes": False})
    return cases
