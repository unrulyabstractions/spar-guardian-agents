"""The SWE sandbox: the amd64 and native images, the compose file, each agent's seat and tools, and the commands the
setup and the grader run in it.

One compose project per sample, every service without network: `default` is the grader, and each agent has a service
of its own name. SWE.check refuses the large scales, whose teams would need a hundred services or more. The native
image is built from sandbox/native.Dockerfile.
"""
from __future__ import annotations

import hashlib
import json
import shlex
from pathlib import Path

from inspect_ai.util import sandbox

from src.domains.swe.swe_inputs import load_split, task_config, task_dir
from src.domains.swe.swe_settings import AGENT_CPUS, AGENT_MEM, BUILD, CHECKER, TEAM_MOUNT, TEAM_REPO, WORKDIR
from src.sandbox import staging
from src.sandbox.agent_tools import Seat
from src.sandbox.docker_images import docker, image_id, image_labels
from src.sandbox.team_gate.team_gate import SERVICE, VOLUME, Gate
from src.swarm.influencers import others_text

# The recipe's text is hashed into the native image's label, so any change to it (its comment line included) rebuilds
# the image, which takes about half an hour. Leave its text as it is.
NATIVE_DOCKERFILE = (Path(__file__).parent / "sandbox" / "native.Dockerfile").read_text()
# The agents' tools name the machine; Inspect's own descriptions do not. No call has a time limit of its own: one still
# running at the end of its round becomes a job (src/sandbox/command_jobs.py, the rounds' rules).
TOOL_DESCRIPTIONS = {
    "shell": (f"Run a bash command on your machine, starting in {WORKDIR}. Each call runs in a fresh shell.",
              "The bash command to run."),
    "run_python": (f"Run Python 3 code on your machine, starting in {WORKDIR}. Print what you want to see.",
                   "The Python code to run."),
}


class SetupFailed(RuntimeError):
    """A setup check failed before any agent started: the sample is an infrastructure failure, to be re-run."""


async def run(box, cmd: str, timeout: int = 600, input: str | bytes | None = None) -> tuple[int, str, str]:
    """`bash -c cmd` in the box: (rc, stdout, stderr)."""
    r = await box.exec(["bash", "-c", cmd], timeout=timeout, input=input)
    return r.returncode, r.stdout or "", r.stderr or ""


async def must(box, cmd: str, what: str, timeout: int = 600) -> str:
    """run(), its stdout, or SetupFailed naming `what`."""
    rc, out, err = await run(box, cmd, timeout)
    if rc != 0:
        raise SetupFailed(f"swe setup: {what} failed (rc={rc}): {err[-1500:] or out[-1500:]}")
    return out


def image_tags(issue: str) -> dict[str, str]:
    repo = load_split(issue)["image"]
    return {"amd64": f"{repo}:amd64", "native": f"{repo}:native"}


def _source_facts(tag: str) -> dict:
    r = docker("image", "inspect", "--format", "{{json .Config.Env}}", tag)
    env = dict(e.split("=", 1) for e in json.loads(r.stdout.decode()))
    r = docker("run", "--rm", "--platform", "linux/amd64", "--network", "none", "--entrypoint", "bash", tag,
               "-c", ". /etc/os-release; echo $VERSION_CODENAME", timeout=600)
    return {"env": env, "codename": r.stdout.decode().strip()}


def native_recipe(tags: dict[str, str], source_id: str) -> tuple[str, str]:
    """The native Dockerfile text and its recipe hash."""
    facts = _source_facts(tags["amd64"])
    env = {k: v for k, v in facts["env"].items() if k != "PATH"}
    env_lines = "\n".join(f"ENV {k}={json.dumps(v)}" for k, v in sorted(env.items()))
    text = NATIVE_DOCKERFILE.format(
        source=tags["amd64"], go_version=env["GOLANG_VERSION"], codename=facts["codename"], env=env_lines,
        path_q=shlex.quote(facts["env"]["PATH"]), source_id=source_id, recipe="RECIPE")
    recipe = hashlib.sha256(text.encode()).hexdigest()[:16]
    return text.replace("RECIPE", recipe), recipe


def ensure_images(issue: str, *, log=print) -> dict[str, str]:
    """Build the amd64 and native images when missing or stale; return their tags and ids.

    Staleness is by label: the native image records the amd64 image id and its recipe hash. A Python task
    raises: its native rebuild would need its dependencies reinstalled.
    """
    instance = load_split(issue)["task"]
    language = task_config(instance).get("repo_language")
    if language != "go":
        raise NotImplementedError(f"{issue}: the native rebuild is implemented for Go tasks only "
                                  f"(repo_language={language!r})")
    tags = image_tags(issue)
    if image_id(tags["amd64"]) is None:
        log(f"swe: building {tags['amd64']} under linux/amd64 emulation")
        docker("build", "--platform", "linux/amd64", "-t", tags["amd64"], str(task_dir(instance) / "environment"))
    source_id = image_id(tags["amd64"])
    text, recipe = native_recipe(tags, source_id)
    labels = image_labels(tags["native"]) or {}
    if labels.get("cbswe.source_image_id") != source_id or labels.get("cbswe.recipe_sha256") != recipe:
        folder = BUILD / issue / "native"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "Dockerfile").write_text(text)
        log(f"swe: building {tags['native']}")
        docker("build", "-t", tags["native"], str(folder))
    return {**tags, "amd64_id": source_id, "native_id": image_id(tags["native"])}


def team_gate(issue: str, kept_out: list[str]) -> Gate:
    """The team remote's gate (src/sandbox/team_gate/): the agents kept out of team/main push through it, as root,
    which owns the remote."""
    return Gate(remote=TEAM_REPO, mount=TEAM_MOUNT, user="root", image=image_tags(issue)["native"],
                kept_out=tuple(kept_out), extra={"cpus": 0.5, "mem_limit": "512m"})


def write_compose(issue: str, agents: list[str], *, team_remote: bool, kept_out: list[str] | None = None,
                  checker: bool = False) -> Path:
    """The grader `default`, one service per agent, and the volume `team`; team_remote mounts it in the agents too
    (a shared_task team), and gives the grader alone CAP_LINUX_IMMUTABLE, which locks the remote
    (_swe_team_remote.LOCK). An agent kept out of team/main mounts it read-only and pushes through the gate, the
    service team_gate.SERVICE (team_gate.Gate). `checker` adds the service CHECKER, where check_fix grades: the
    grader's image, limits and hostname, with no volume and no capability added, so the team's code it tests reaches
    no other machine."""
    image = image_tags(issue)["native"]
    grading = {"working_dir": WORKDIR, "image": image, "hostname": "grader", "cpus": 4.0, "mem_limit": "6g"}
    gate = team_gate(issue, kept_out or [])
    locks = {"cap_add": ["LINUX_IMMUTABLE"]} if team_remote else {}
    services = {"default": staging.compose_service(**grading, volumes=[f"{VOLUME}:{TEAM_MOUNT}"], **locks)}
    if checker:
        services[CHECKER] = staging.compose_service(**grading)
    for a in agents:
        services[a] = staging.compose_service(working_dir=WORKDIR, image=image, hostname=a, cpus=AGENT_CPUS,
                                              mem_limit=AGENT_MEM, restart="unless-stopped",
                                              **({"volumes": gate.agent_volumes(a)} if team_remote else {}))
    if team_remote and gate.kept_out:
        services[SERVICE] = gate.service()
    return staging.write_team_compose(BUILD / issue, "compose-board" if team_remote else "compose", services,
                                      {VOLUME: {}, **(gate.volumes() if team_remote else {})})


def seats(agents: list[str]) -> dict[str, Seat]:
    """Each agent's own service, as the service's own user. Nothing is reaped after a command, since each agent is
    alone on its machine; what its calls left running dies at the end of its round (SWE.round_end,
    command_jobs.reap_round)."""
    return {a: Seat(service=a, round_reap=True) for a in agents}


def others_script(path: str) -> str:
    """The commands that make a disregulator's OTHERS.md (Benchmark.others_files) its own: root's at mode 0600, since
    the agent is root on a machine of its own, and kept out of git in its clone (staging.private_in_git), so it never
    reaches the team remote."""
    return f"chown root:root {shlex.quote(path)} && {staging.private_in_git(path)}"


async def plant_others(others: dict[str, dict]) -> None:
    """Write each disregulator's OTHERS.md (the swe block's `others`) on its own machine, with the text
    influencers.others_text gives its names, and exclude it from git there (others_script). SetupFailed when a check
    fails."""
    for agent, entry in others.items():
        box = sandbox(agent)
        await box.write_file(entry["path"], others_text(entry["names"]))
        await must(box, others_script(entry["path"]), f"OTHERS.md in {agent}")


async def install_gate(swe: dict) -> None:
    """On each machine kept out of team/main (the swe block's shared_result), the gate's shim, proved through the gate
    (team_gate.Gate.install). SetupFailed when a check fails."""
    gate = team_gate(swe["issue"], (swe.get("shared_result") or {}).get("kept_out") or [])
    for agent in gate.kept_out:
        try:
            await gate.install(sandbox(agent))
        except RuntimeError as error:
            raise SetupFailed(f"swe setup: {agent}: {error}") from error
