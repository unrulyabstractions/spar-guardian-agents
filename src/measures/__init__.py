"""Measures over run directories: the episode summary, the code ladder with its team record and the scales it shares
with the judged ladder, Lucy's SWE measures, the board engagement funnel and the figures.

API modules:
- run_directory.py: RunDir, one run directory read through one object.
- episode_summary.py: one line per episode, and the turns of lone agents' runs.
- code_ladder.py: the cheating signals from a run's transcripts, the sandbox probe and its records, without a judge.
- team_ladder.py: the team's record on the code ladder, one per sample.
- ladder_domain.py: Domain, the base class of what a domain tells the code ladder.
- rungs.py: the ladder's scales, shared by the judged ladder, the code ladder and the team record.
- honeypot_fingerprints.py: the honeypot's fingerprints and the probe that finds them in a sandbox.
- shell_commands.py: shell commands as the code ladder reads them.
- swe_measures.py: Lucy's measures for SWE runs.
- board_engagement.py: the board engagement funnel, per agent and per role.
- verdict_fields.py: each task's judge flags, in order with cheated last.
- run_figures.py: the static figures of one run.
"""
