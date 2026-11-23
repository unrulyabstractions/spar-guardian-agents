Each folder is an issue's second content: A.patch, B.patch and C.patch cut the same files into the same pieces as the planted branches, and content.yaml holds the values the hidden tests check and the ladder's thresholds.
To change the content, edit those four files; swe_inputs.second reads them and no code needs to change.
Then run `uv run pytest tests/unit -k "collaboration or second" -q` (set the thresholds from what test_no_stored_diff_reaches_a_second_threshold prints) and `uv run python -m tests.smoke.swe --only second_grading,second_ladder`.
