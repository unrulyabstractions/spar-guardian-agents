"""The team remote's gate: machines kept out of the remote's main push through it, and every other agent as before.

A team remote is a bare repository on a volume that every agent's machine mounts. A machine kept out of main mounts it
read-only, so nothing run there, as root or not, writes the remote. It pushes through the gate instead: the service
SERVICE runs gate_server.py as root, with the volume writable, and serves each kept-out agent on a Unix socket of a
volume that only that agent and the gate mount. The gate runs `git receive-pack` with the agent's git identity, as the
remote's own user, and its update hook refuses each protected ref with git's plain refusal (prompts.yaml
team_remote.protected), naming no role. Every other ref goes through, so the agent still pushes branches of its own.
On the machine, git-receive-pack is replaced by receive_pack.py, which sends a push of the team remote to the socket
and runs git's own for any other path; the remote's URL, `git remote -v` and every text the agent reads stay as they
were.

    gate = Gate(remote="/team/repo.git", mount="/team", user="root", kept_out=("k7q2m",), image=image)
    services = {**agents, **({SERVICE: gate.service()} if gate.kept_out else {})}
    volume_list = gate.agent_volumes(agent)       # ["team:/team"], or ["team:/team:ro", "<socket volume>"] kept out
    volumes = {"team": {}, **gate.volumes()}
    await gate.install(sandbox(agent))           # in the setup, on each kept-out machine, as root
"""
from __future__ import annotations

import json
import shlex
from dataclasses import dataclass, field
from pathlib import Path

from src.configuration.settings import load_prompts
from src.sandbox import staging

HERE = Path(__file__).parent
SERVER = (HERE / "gate_server.py").read_text()
RECEIVE_PACK = (HERE / "receive_pack.py").read_text()
SERVICE = "team_gate"
VOLUME = "team"                         # the team remote's volume, as each domain's compose file names it
SOCKET_DIR = "/run/team-remote"         # on a kept-out machine: its socket volume
SOCKET = f"{SOCKET_DIR}/push.sock"
GATE_DIR = "/run/team-gate"             # in the gate: one socket volume per kept-out agent, by agent
HOW = "read_only_mount_and_gate"        # how a domain's run.json shared_result says the rule is enforced
EMAIL = "{agent}@team.internal"         # the git identity each domain's setup gives its agents
SHIM_STAGE = "/tmp/.receive-pack.py"    # where the setup writes the shim before INSTALL moves it into git
# Install the shim as root on a kept-out machine, then prove the path: an empty push session through the gate must
# get receive-pack's advertisement, which names report-status even for a remote with no commit yet.
INSTALL = """set -e
target="$(git --exec-path)/git-receive-pack"
python="$(command -v python3)"
rm -f "$target"
{{ printf '#!%s -I\\n' "$python"; cat {stage}; }} > "$target"
rm -f {stage}
chmod 755 "$target"
for i in $(seq 1 60); do [ -S {socket} ] && break; sleep 0.5; done
printf '0000' | "$target" {remote} | grep -q report-status
"""


@dataclass(frozen=True)
class Gate:
    """One team remote's gate: where the remote lives, whose user owns it, its protected branch and the agents kept
    out of that branch."""

    remote: str
    """The bare repository's path in every machine."""
    mount: str
    """Where every machine mounts the remote's volume."""
    user: str
    """The remote's own user, which the gate's receive-pack runs as: root where the agents are root, else theirs."""
    image: str
    """The gate service's image: one of the sample's own, with git and Python 3."""
    kept_out: tuple[str, ...] = ()
    """The agents that may not write the protected branch, in roster order."""
    branch: str = "main"
    """The protected branch: the shared result a submit hands in."""
    extra: dict = field(default_factory=dict)
    """More keys of the gate service (limits)."""

    @property
    def protected(self) -> list[str]:
        return [f"refs/heads/{self.branch}"]

    def socket_volume(self, agent: str) -> str:
        return f"gate_{agent}"

    def agent_volumes(self, agent: str) -> list[str]:
        """The agent's mounts: the remote's volume, read-only for a kept-out agent, which also mounts its socket."""
        if agent not in self.kept_out:
            return [f"{VOLUME}:{self.mount}"]
        return [f"{VOLUME}:{self.mount}:ro", f"{self.socket_volume(agent)}:{SOCKET_DIR}"]

    def volumes(self) -> dict[str, dict]:
        """The socket volumes, for the compose file's top-level volumes."""
        return {self.socket_volume(agent): {} for agent in self.kept_out}

    def config(self) -> dict:
        """gate_server.py's config."""
        return {"remote": self.remote, "user": self.user, "email": EMAIL, "protected": self.protected,
                "message": load_prompts()["team_remote"]["protected"],
                "agents": {agent: f"{GATE_DIR}/{agent}/{Path(SOCKET).name}" for agent in self.kept_out}}

    def service(self) -> dict:
        """The gate's compose service: the remote's volume writable, every socket volume, and the server as its
        command."""
        mounts = [f"{VOLUME}:{self.mount}", *(f"{self.socket_volume(a)}:{GATE_DIR}/{a}" for a in self.kept_out)]
        return staging.compose_service(working_dir="/", image=self.image, hostname=SERVICE, volumes=mounts,
                                       restart="unless-stopped",
                                       command=["python3", "-I", "-c", SERVER, json.dumps(self.config())],
                                       **self.extra)

    def install_script(self) -> str:
        """The shell lines that install the shim on a kept-out machine and prove the gate answers (INSTALL)."""
        return INSTALL.format(stage=SHIM_STAGE, socket=shlex.quote(SOCKET), remote=shlex.quote(self.remote))

    def shim(self) -> str:
        """receive_pack.py with the remote and the socket filled in."""
        return RECEIVE_PACK.replace("{remote}", self.remote).replace("{socket}", SOCKET)

    async def install(self, box) -> None:
        """Install the shim on a kept-out machine as root and prove the gate answers; RuntimeError otherwise."""
        await box.write_file(SHIM_STAGE, self.shim())
        r = await box.exec(["bash", "-c", self.install_script()], user="root", timeout=120)
        if r.returncode != 0:
            raise RuntimeError(f"the team remote's gate failed: {(r.stderr or r.stdout or '')[-500:]}")
