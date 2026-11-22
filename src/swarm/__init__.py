"""The swarm on ORBIT: the board, the influencers, the regulator's oversight tool, Lucy's turn rules and the
end-of-episode probes.

API modules:
- board.py: one shared channel as ORBIT configuration, and the pull board's tools.
- influencers.py: workers and the influencers (regulators and disregulators) as peers on one board, and the
  instruction levels.
- submit_and_stop.py: the tools that end an agent's work.
- awareness_probes.py: Lucy's end-of-episode questions as an Inspect scorer.
- turn_rules/: the plan of rounds, the halt and the model filter.
"""
