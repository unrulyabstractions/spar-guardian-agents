"""What /proc shows an agent: only its own processes, and no page map.

API modules:
- proc_hiding.py: the switch, the AppArmor profile and the compose file, the setup solver, its proof and its record.

host_helper.py runs in a privileged helper container on the Docker host; proc_group.py and proc_proof.py run inside an
agent's machine; agent_profile.apparmor is the profile's template. proc_hiding.py sends them.
"""
