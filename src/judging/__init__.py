"""The LLM judges: CheatBench's verdict per agent, the Collusion ladder's scheming judge per run's board (each colluding
pair's verdict derived from it in code), and the judge labels awareness and manipulation per agent.

API modules:
- cheatbench_judge.py: CheatBench's judge, run in process through Inspect's model API (the eval's scorer, `judge`,
  `rejudge`), and the one way any of our judges makes a call (ask, verdict_row, Unit).
- scheming_judge.py: the pass "scheming": one call per run in which code places a pair at collusion, on its board, its
  prompt scheming_prompt.md, and the code check of each flagged post's quote.
- labels_judge.py: the pass "labels": one call per agent that qualifies for awareness or manipulation, its prompt
  labels_prompt.md with each question's text (labels_awareness.md, labels_manipulation.md), and the code checks of its
  quotes.
- judge_records.py: what those two judges read of a run (the board, the instructions, the visible reasoning, the
  actions), the quote check, and pair_scheming, a pair's scheming derived from the pass's board row.
- judge_pass.py: `python -m src judge-pass`, a pass over runs' stored records under a dollar cap.
- verdict_files.py: the one reader and writer of runs/<study>/<run>/judges/<pass>.<judge>.jsonl, and the one reader of
  CheatBench's verdicts on a run (the eval's judge.jsonl with the passes' files).
"""
