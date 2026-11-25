"""The team remote's gate: agents kept out of a remote's main push through it (team_gate.py).

API modules:
- team_gate.py: the gate's compose service, each machine's mounts, the shim's install and the run's record.

gate_server.py runs in the gate service and receive_pack.py on each kept-out machine; team_gate.py sends both.
"""
