The second content of creative writing's exploit: a poem per brief, `<row id>.md`, on the same brief as CheatBench's reference.
A variant with `second_content` cuts it into the side copies; the grader's endpoint, the scorer and the ladder read it through `_creative_honeypot.content_text(row, "second")`.
To swap it, edit the file: keep the brief's form, no line or name of the reference, and the same number of lines, then run `uv run pytest tests/unit -k "creative or collaboration" -q`.
A stored run that planted another text refuses to be read by the ladder, which names the mismatch; `python -m src pipeline status` then asks for a re-lock.
