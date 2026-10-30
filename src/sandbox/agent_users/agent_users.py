"""One Linux user per agent in one shared container: the seats, the private temporary directories, the loopback
guard, the end state and the deviations this layout records. openmath, knowledge work and creative writing give a
team this layout.

    seats = team_seats(agents, lambda a: f"/workspace/{a}")
    setup = private_temp(agents)                      # appended to the sample's setup script
    report = await guard(seats, allow=[f"{MIRROR_IP}:443"])     # in the setup solver, before any agent runs

Each agent has a user of its own name, uid FIRST_UID upward, home /home/<name> at mode 700, and its tools run as that
user, its processes killed after each command (src/sandbox/agent_tools.py). Where each agent works in a copy of its
own, the agent users' shells create private files (umask 077) and /tmp, /var/tmp and /dev/shm are writable but not
listable (mode 1733), so a file passed through /tmp takes its owner naming its path to the others, which the
transcript shows and the probe records by owner. The setup proves it and refuses the layout otherwise. Loopback
traffic between the agent users is refused by iptables owner rules (src/sandbox/sandbox_hygiene.py LOOPBACK_GUARD).
A lone agent keeps CheatBench's layout: the user `user` (lone_seat).
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from src.sandbox import sandbox_hygiene as hygiene
from src.sandbox.agent_tools import AGENT_NAME, AGENT_UID, WORKDIR, Seat

FIRST_UID = hygiene.AGENT_UID_FROM
PRIVATE_TEMP = (Path(__file__).parent / "private_temp.sh").read_text()
# How a team in this layout departs from CheatBench's runner.
USER_DEVIATIONS = [
    "each_agent_has_its_own_linux_user_and_home_in_one_container_with_one_clock",
    "tools_shell_and_run_python_bound_per_agent_user_in_place_of_bash_and_python",
    "loopback_refused_between_agent_users_by_iptables_owner_rules",
    "agent_user_processes_killed_after_each_command_and_at_the_end",
]
PRIVATE_TEMP_DEVIATION = "temporary_directories_not_listable_and_files_private_by_default"


def lone_seat(workdir: str = WORKDIR) -> Seat:
    """CheatBench's sandbox user, for a lone agent: its tools are ORBIT's own, so nothing is reaped."""
    return Seat(user=AGENT_NAME, uid=AGENT_UID, home=f"/home/{AGENT_NAME}", workdir=workdir)


def team_seats(agents: list[str], workdir_of: Callable[[str], str]) -> dict[str, Seat]:
    """Each agent's user, uid, home and working directory, uids from FIRST_UID in roster order, which is sorted by ID,
    so that a uid or the order of /etc/passwd shows no seat."""
    return {a: Seat(user=a, uid=FIRST_UID + i, home=f"/home/{a}", workdir=workdir_of(a), reap=True)
            for i, a in enumerate(agents)}


def private_temp(agents: list[str]) -> str:
    """The setup block that makes the agent users' files private and the temporary directories unlistable, then
    proves it: the first agent writes a file, and the second must fail to read it or list /tmp."""
    return PRIVATE_TEMP.format(first=agents[0], second=agents[1]).rstrip("\n")


async def guard(seats: dict[str, Seat], *, allow: list[str] | None = None) -> dict:
    """Refuse loopback between the agent users, proven on the first two; the report for hygiene.GUARD_STORE.
    Raises when the guard cannot hold, so the layout is refused before any agent runs."""
    first, second = [seat.user for seat in seats.values()][:2]
    return await hygiene.guard_loopback(first, second, allow=allow)


async def end_state(owners: dict[str, int], dirs: list[str]) -> dict:
    """The container at the end of the run (hygiene.sandbox_state): the processes of each owner ({name: uid}), then
    killed, its sockets, and who owns the files of the temporary directories and `dirs`."""
    return await hygiene.sandbox_state(owners, list(dict.fromkeys(hygiene.STATE_DIRS + dirs)))
