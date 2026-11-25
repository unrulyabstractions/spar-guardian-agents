"""The network log: the access log's sidecar also records every socket call in the agents' machines.

API modules:
- network_monitor.py: the tracer's volume and mounts, the setup solver that starts the monitor and its control, and
  the capture of its rows.

network_monitor_daemon.py runs inside the sidecar beside access_monitor_daemon.py, whose /proc readers it imports;
network_monitor.py sends both.
"""
