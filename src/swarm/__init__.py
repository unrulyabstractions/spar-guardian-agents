"""The swarm on ORBIT: the board, the influencers, the regulator's oversight tool, Lucy's turn rules, the seed
packs' setup and the probes.

API modules:
- board.py: one shared channel as ORBIT configuration, and the pull board's tools.
- influencers.py: workers and the influencers (regulators and disregulators) as peers on one board, and the
  instruction levels.
- submit_and_stop.py: the tools that end an agent's work, and an influencer's alert.
- submit_hold.py: a regulator's hold on the team's hand-in.
- end_round.py: the tool that ends an agent's round now.
- seed_setup.py: a seed pack's posts and files, before round 1.
- tool_output_caps.py: how much of one tool result an agent sees.
- awareness_probes.py: Lucy's end-of-episode questions as an Inspect scorer.
- belief_probes.py: what a worker believes about its task's exploits before it acts, as an Inspect scorer.
- turn_rules/: the plan of rounds, the halt and the model filter.
"""
