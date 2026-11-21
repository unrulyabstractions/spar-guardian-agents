"""The LLM judges: CheatBench's verdict per agent, and the two judge labels, disclosure and manipulation, each a score
with an excerpt per agent.

API modules:
- cheatbench_judge.py: CheatBench's judge, run in process through Inspect's model API (the eval's scorer, `judge`,
  `rejudge`), and the one way any of our judges makes a call (ask, verdict_row, Unit).
- labels_judge.py: the pass "labels": one call per agent of a run, its prompt labels_prompt.md with each label's
  definition (labels_disclosure.md, labels_manipulation.md).
- judge_records.py: what the labels judge reads of a run (the board, the instructions, the visible reasoning, the
  actions with their results), and where code locates the judge's excerpt in the agent's own text.
- judge_pass.py: `python -m src judge-pass`, a pass over runs' stored records under a dollar cap.
- verdict_files.py: the one reader and writer of runs/<study>/<run>/judges/<pass>.<judge>.jsonl, and the one reader of
  CheatBench's verdicts on a run (judges/cheatbench.<judge>.jsonl: the eval's judge and the passes).
"""
