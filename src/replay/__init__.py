"""A stored run played back in its sandbox from its recorded tool calls alone: no model call, no network, no key. The
replay rebuilds the state after every call, which the record keeps only at the end, so a measure can read each rung
at the call that earned it. It plays back software engineering, protein design and creative writing runs.

    python -m src replay <run or study ...> --out <dir> [--only-missing] [--serial] [--pem] [--warm-image]
    report = replay(run_dir, out_dir)          # src/replay/replay_run.py; out_dir/steps.jsonl and replay.json
    steps = faithful_steps(out_dir)            # src/replay/replay_report.py; refuses an unfaithful replay

How. The record gives the calls (the transcripts: each call's agent and number k; the eval log: arguments, output,
times). The setup is the run's own (SWE: src/domains/swe/swe_replay.py; protein design and creative writing: their
domains' staging), and the replay refuses a run whose setup comes out other than run.json records it (SWE's refs and
planted commits, protein's staged files, creative's staging and probe). Each sandbox call then runs in its agent's
machine as Inspect ran it, as its seat's user where the task has one, in the recorded order. A commit the call makes
gets the date in the call's window that gives its recorded id (commit_dates.py), so later commands that name it find
it. Host tools (board_read, board_post, board_status, submit, stop_working, a regulator's oversight tools) are replayed
from their recorded outputs: they change no machine. Two do run: protein design's check_design, re-run on the host on
its recorded arguments, and creative writing's submit, which keeps writing.md. At the end the grader's own commands
read the end state, which must equal the stored one.

steps.jsonl. One line per call in global order. An SWE line:

    {"n": 12, "agent": "k7q2m", "k": 4, "call_id": "call_...", "t": 81.4, "tool": "shell", "match": "exact",
     "state": {"head": "<sha>", "branch": "k7q2m", "refs": {...}, "team_refs": {...},
               "pieces": {"A": {"tree": false, "team": [], "refs": ["refs/remotes/origin/rd/redis-tls-config"],
                                "lines": [0, 3], "whole": []}, ...},
               "dirty": false}}

- n counts every call from 1; k is the agent's own index from 0, as signals.jsonl numbers its calls; t is seconds from
  the run's first call, by the eval log's clock.
- match: exact, masked (equal once times and durations are masked), order (the same lines in another order), differs,
  or recorded (a host tool, or a call whose arguments the tool refused, so nothing ran).
- refs and team_refs (ref name -> commit id) hold only what changed since the agent's previous line, a removed ref as
  null, and the whole map on the agent's first line. Every other key is the value in full.
- pieces: for each piece, whether the agent's working tree holds it (SweDomain.holds on the tree the grader's `git add
  -A` takes, against the base, with no seen gate), the team remote's refs that hold it and the agent's own refs that do
  (its tags and the upstream branches the setup left are not read). With them, what holds reads of the tree, so a
  reader can apply the seen gate the end reading applies (SweDomain.tree_holds): "lines", the indices of the piece's
  distinctive lines (sorted) the tree's diff adds, and "whole", its planted files the tree adds whole. A steps.jsonl
  written before these keys has only the flag. dirty: the tree differs from HEAD's.
- The keys of state are the task's (each replayer's line_state). Protein design's lines carry SWE's keys, then files
  (every file of the agent's machine that holds a piece or the whole design), ref_designs, team_designs and check (the
  check_design call's reading, on its own line alone, else null): protein_replay.py. Creative writing's lines carry
  the shared files and the pieces each holds, each workspace's hand-in, the call's checks and the pieces its output
  showed or its arguments said: creative_replay.py. A reader takes the keys it knows.

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
- replay_sandbox.py: the compose project and Inspect's tool exec, as a seat's user.
- git_state.py: what the git tasks share: the calls that touch the team remote, a line's git keys.
- swe_replayer.py: the SWE run's setup, state and end state.
- protein_replay.py: the protein design run's.
- creative_replay.py: the creative writing run's.
- random_content.py: the opt-in swap of drawn certificates and keys for the recorded ones (--pem).
- warm_image.py: the opt-in run image with Go's build cache filled (--warm-image).
"""
