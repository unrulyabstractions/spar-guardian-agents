"""A stored run played back in its sandbox from its recorded tool calls alone: no model call, no network, no key. The
replay rebuilds the state after every call, which the record keeps only at the end, so a measure can read each rung
at the call that earned it. SWE only, so far.

    python -m src replay <run or study ...> --out <dir> [--only-missing] [--serial]
    report = replay(run_dir, out_dir)          # src/replay/replay_run.py; out_dir/steps.jsonl and replay.json
    steps = faithful_steps(out_dir)            # src/replay/replay_report.py; refuses an unfaithful replay

How. The record gives the calls (the transcripts: each call's agent and number k; the eval log: arguments, output,
times). The setup is the run's own (src/domains/swe/swe_replay.py), and the replay refuses a run whose refs or
planted commits come out other than run.json records them. Each sandbox call then runs in its agent's machine as
Inspect ran it, in the recorded order. A commit the call makes gets the date in the call's window that gives its
recorded id (commit_dates.py), so later commands that name it find it. Host tools (board_read, board_post,
board_status, submit, stop_working, a regulator's agent_actions) are replayed from their recorded outputs: they change
no machine. At the end the grader's own commands read the end state, which must equal the stored one byte for byte.

steps.jsonl. One line per call in global order:

    {"n": 12, "agent": "k7q2m", "k": 4, "call_id": "call_...", "t": 81.4, "tool": "shell", "match": "exact",
     "state": {"head": "<sha>", "branch": "k7q2m", "refs": {...}, "team_refs": {...},
               "pieces": {"A": {"tree": false, "team": [], "refs": ["refs/remotes/origin/rd/redis-tls-config"]}, ...},
               "dirty": false}}

- n counts every call from 1; k is the agent's own index from 0, as signals.jsonl numbers its calls; t is seconds from
  the run's first call, by the eval log's clock.
- match: exact, masked (equal once times and durations are masked), order (the same lines in another order), differs,
  or recorded (a host tool, or a call whose arguments the tool refused, so nothing ran).
- refs and team_refs (ref name -> commit id) hold only what changed since the agent's previous line, a removed ref as
  null, and the whole map on the agent's first line. Every other key is the value in full.
- pieces: for each piece, whether the agent's working tree holds it (SweDomain.holds on the tree the grader's `git add
  -A` takes, against the base, with no seen gate), the team remote's refs that hold it and the agent's own refs that do
  (its tags and the upstream branches the setup left are not read). dirty: the tree differs from HEAD's.
- The keys of state are the task's. Another task adds its own beside these (a shared workspace's files, a checker's
  inputs); a reader takes the keys it knows.

replay.json holds the counts of each match, the likely causes of each difference, the dated commits, the setup's
check, the end state part by part, the image (and whether it is the run's own), wall seconds by phase and `faithful`.
A reader of steps.jsonl goes through faithful_steps, which refuses a replay whose end state differs.

API modules:
- replay_run.py: replay(run_dir, out_dir) and replay_runs, the command's loop.
- replay_report.py: ReplayReport, replay.json, and the faithful gate.
- step_snapshots.py: steps.jsonl's delta encoding.
- recorded_calls.py: the recorded calls in global order.
- commit_dates.py: the date that gives a commit its recorded id.
- output_match.py: a replayed output against the recorded one.
- replay_sandbox.py: the compose project and Inspect's tool exec.
- swe_replayer.py: the SWE run's setup, state and end state.
"""
