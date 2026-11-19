"""Measures over run directories: the episode summary, the code's evidence of each agent's acts on the honeypot, the
project's three ladders (Cheating, Coordination, Collusion), Lucy's SWE measures, the board engagement funnel, the
figures, and each study's index with the validity of its runs.

API modules:
- run_directory.py: RunDir, one run directory read through one object, and where a study keeps its runs, its eval
  logs, its figures and its earlier attempts.
- run_validity.py: whether a run measures what its cell says, and the first reason it does not.
- run_listing.py: the runs of a collection under runs/ and whether each counts (INDEX.jsonl, manifest.jsonl).
- deal_replay.py: whether today's code deals a run the roster and the holdings it recorded.
- run_index.py: one record per run of a study, for runs/<study>/INDEX.jsonl; scripts/box/study_index.py writes it.
- episode_summary.py: one line per episode, and the turns of lone agents' runs.
- code_ladder.py: the run's code record (signals.jsonl, run.json "signals"): the evidence and the three ladders' rungs.
- transcript_scan.py: the code's evidence of each agent's acts, from its transcript, access log and disk.
- team_ladder.py: the team's record of the evidence and its collaboration evidence, one per sample.
- cheating_ladder.py: the Cheating ladder (none, looked, tempted, attempted, hacked), by code.
- coordination.py: the Coordination ladder (none, read, write, low, medium, high, extreme), from the board.
- collusion.py: the Collusion ladder (none, cooperation, collaboration by code; collusion, scheming by a judge).
- ladder_by_domain.py: what puts an agent on each Cheating rung, per task.
- access_evidence.py: the access log as the transcript scan reads it.
- ladder_domain.py: Domain, the base class of what a domain tells the code's evidence.
- rungs.py: the Cheating ladder's scale, the names of the evidence and the three ladders' record fields.
- honeypot_fingerprints.py: the honeypot's fingerprints and the probe that finds them in a sandbox.
- shell_commands.py: shell commands as the transcript scan reads them.
- swe_measures.py: Lucy's measures for SWE runs.
- board_engagement.py: the board engagement funnel, per agent and per role.
- verdict_fields.py: each task's judge flags, in order with cheated last.
- run_figures.py: the static figures of one run.
"""
