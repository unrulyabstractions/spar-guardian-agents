"""The access log: one sidecar per sample records each open of the honeypot, outside the agents' machines.

API modules:
- access_monitor.py: what a domain watches, the sidecar, the solver that starts the monitor and the capture of its rows.

access_monitor_daemon.py and git_object_files.py run inside a container; access_monitor.py sends them. The export
imports exec_digest from the daemon's file, so the two sides of that hash cannot drift apart.
"""
