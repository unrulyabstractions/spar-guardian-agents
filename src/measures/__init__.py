"""Measures over run directories: the episode summary, the code's evidence of each agent's acts on the honeypot, the
project's three ladders (Cheating, Coordination, Collusion), Lucy's SWE measures, the board engagement funnel, the
figures, and each study's index with the validity of its runs.

API modules:
- run_directory.py: RunDir, one run directory read through one object, and where a study keeps its runs, its eval
  logs, its figures and its earlier attempts.
- run_validity.py: whether a run measures what its cell says, and the first reason it does not.
- run_listing.py: the runs of a collection under runs/ and whether each counts (INDEX.jsonl, manifest.jsonl).
- pipeline_parts.py, stored_versions.py, stored_json.py: the pipeline's registry of parts, each run's two ledgers, and
  the one serializer of what processing stores.
- process_runs.py: `python -m src process`: every part behind the registry made and stored in the run's own folder.
- topology_fact.py: the run's communication topology, a fact with no label (processed/facts/topology.json).
- deal_replay.py: whether today's code deals a run the roster and the holdings it recorded.
- run_index.py: one record per run of a study, for runs/<study>/INDEX.jsonl; scripts/study/study_index.py writes it.
- episode_summary.py: one line per episode, and the turns of lone agents' runs.
- code_ladder.py: the run's code record (signals.jsonl, run.json "signals"): the evidence and the three ladders' rungs,
  the pipeline's `signals` part (signals_part, run_signals), and the reader of the three recorded rungs (ladders).
- transcript_scan.py: the code's evidence of each agent's acts, from its transcript, access log and disk.
- team_ladder.py: the team's record of the evidence and its travel evidence, one per sample.
- cheating_ladder.py: the Cheating ladder (none, looked, tempted, attempted, hacked), by code.
- coordination.py: the Coordination ladder (none, read, write, low, medium, high, extreme), from the board.
- collusion.py: the Collusion ladder (none, cooperation, collaboration, collusion), by code.
- collusion_takes.py: the Collusion ladder's take rules per task (TASK_RULES), each with its rule stated as data.
- run_calls.py: the run's calls and boards as the take rules read them (load_run), and the take they find.
- git_takes.py, text_takes.py, file_takes.py: the generic take rules: git, text and files.
- judge_labels.py: the judges beside the ladders: CheatBench's labels beside the Cheating clauses, CheatBench's
  disclosed access, and the judge labels disclosure and manipulation, each a score with an excerpt per agent.
- ladder_by_domain.py: what puts an agent on each Cheating rung, per task the study reads, and the tasks it does not.
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
