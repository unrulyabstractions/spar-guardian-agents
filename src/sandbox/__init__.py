"""What every domain builds its sandbox from. Nothing here knows a domain. agent_users/ gives one Linux user per agent
and machines/ one machine per agent.

API modules:
- agent_tools.py: the agents' tools and the seats they are bound to.
- cheatbench_task_files.py: CheatBench's task folder and its built rows, as every domain reads them.
- docker_images.py: Docker images on the host, built from a recipe when the label no longer names it.
- sandbox_hygiene.py: the processes an agent leaves, the loopback channel and what the sandbox holds at the end.
- sandbox_probe.py: one command's record, a table of commands, and the setup solver that probes the seats.
- staging.py: files written on the host for a sandbox: atomic writes, compose files and staging tarballs.
- task_tool_store.py: the sample store of a task's own tool.

team_remote_lock.sh runs inside a container; staging.py sends it.
"""
