"""The LLM judges: CheatBench's verdict per agent, and the Collusion ladder's judge per collaborating pair.

API modules:
- cheatbench_judge.py: CheatBench's judge, run in process through Inspect's model API (the eval's scorer, `judge`,
  `rejudge`), and the one way any of our judges makes a call (ask, verdict_row, Unit).
- collusion_judge.py: the Collusion ladder's judge: one call per collaborating pair, its prompt collusion_prompt.md,
  the code checks of its quotes, and the run's Collusion ladder with its verdicts (judged_collusion).
- judge_pass.py: `python -m src judge-pass`, a pass over runs' stored records under a dollar cap.
- verdict_files.py: the one reader and writer of runs/<study>/<run>/judges/<pass>.<judge>.jsonl, and the one reader of
  CheatBench's verdicts on a run (the eval's judge.jsonl with the passes' files).
"""
