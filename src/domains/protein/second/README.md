# The second content of ian_binder

Under `Variant.second_content` each agent's side copy holds its segment of these six designs per target, and
check_design accepts them as it accepts the planted ones. Staging, the checker and the ladder read them through
`candidates(target)` alone.

To change them, edit the parameters at the top of `second_binders.py` (no other code changes), then run
`uv run pytest tests/unit/test_protein_second.py tests/unit -k collaboration -q`.
