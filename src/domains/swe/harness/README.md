# The SWE split and its pieces

`splits/<issue>.yaml` says how an issue's upstream fix is cut into the pieces A, B and C and where each is planted.
`pieces/<issue>/` holds each piece as a patch (`A.patch`, `B.patch`, `C.patch`), the whole fix (`whole.patch`), the
lines that tell the pieces apart (`lines.json`) and the check of the cut (`check.json`).

These 21 files are Lucy's, copied unchanged from her repository at commit 449fbf0, folder
`experiments/orbit_cheatbench/`. The SWE task reads this copy (`swe_settings.HARNESS`), so a run needs no checkout
of her repository. `tests/unit/test_swe_harness_copy.py` compares the copy with her files when her repository is
checked out at `submodules/lucy`.
