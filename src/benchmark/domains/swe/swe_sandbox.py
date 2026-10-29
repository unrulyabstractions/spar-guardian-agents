"""The sandbox: the amd64 and native images, the compose file and the tools bound to each agent's service.

One compose project per sample, every service without network: `default` is the grader, and each agent has a service
of its own name. SWE.check refuses the large scales, whose teams would need a hundred services or more.
The native image is built from scripts/native.Dockerfile.
"""
from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
from pathlib import Path

import yaml
from inspect_ai.tool import ToolDef, bash, python
from orbit.tools import ToolContext, ToolFactory

from src.benchmark.domains.swe.swe_inputs import load_split, task_config, task_dir
from src.benchmark.domains.swe.swe_settings import AGENT_CPUS, AGENT_MEM, BUILD, TOOL_TIMEOUT_S, WORKDIR

# The recipe's text is hashed into the native image's label, so any change to it (the comment line included)
# rebuilds the image, which takes about half an hour. The comment line keeps the module's old path for that reason.
NATIVE_DOCKERFILE = (Path(__file__).parent / "scripts" / "native.Dockerfile").read_text()


def _docker(*args: str, check: bool = True, timeout: int = 3600) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], check=check, capture_output=True, timeout=timeout)


def _image_id(tag: str) -> str | None:
    r = _docker("image", "inspect", "--format", "{{.Id}}", tag, check=False)
    return r.stdout.decode().strip() if r.returncode == 0 else None


def _label(tag: str, key: str) -> str | None:
    r = _docker("image", "inspect", "--format", "{{json .Config.Labels}}", tag, check=False)
    if r.returncode != 0:
        return None
    return (json.loads(r.stdout.decode() or "null") or {}).get(key)


def image_tags(issue: str) -> dict[str, str]:
    repo = load_split(issue)["image"]
    return {"amd64": f"{repo}:amd64", "native": f"{repo}:native"}


def _source_facts(tag: str) -> dict:
    r = _docker("image", "inspect", "--format", "{{json .Config.Env}}", tag)
    env = dict(e.split("=", 1) for e in json.loads(r.stdout.decode()))
    r = _docker("run", "--rm", "--platform", "linux/amd64", "--network", "none", "--entrypoint", "bash", tag,
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
    if _image_id(tags["amd64"]) is None:
        log(f"swe: building {tags['amd64']} under linux/amd64 emulation")
        _docker("build", "--platform", "linux/amd64", "-t", tags["amd64"], str(task_dir(instance) / "environment"),
                timeout=7200)
    source_id = _image_id(tags["amd64"])
    text, recipe = native_recipe(tags, source_id)
    if (_label(tags["native"], "cbswe.source_image_id") != source_id
            or _label(tags["native"], "cbswe.recipe_sha256") != recipe):
        folder = BUILD / issue / "native"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "Dockerfile").write_text(text)
        log(f"swe: building {tags['native']}")
        _docker("build", "-t", tags["native"], str(folder), timeout=7200)
    return {**tags, "amd64_id": source_id, "native_id": _image_id(tags["native"])}


def _service(image: str, hostname: str, **extra) -> dict:
    return {"image": image, "init": True, "command": ["sleep", "infinity"], "working_dir": WORKDIR,
            "network_mode": "none", "stop_grace_period": "1s", "x-local": True, "hostname": hostname, **extra}


def write_compose(issue: str, agents: list[str], *, board: bool) -> Path:
    """The grader `default`, one service per agent, and the volume `team`. board True mounts it in the agents too: a
    team with the team remote (shared_task)."""
    image = image_tags(issue)["native"]
    services = {"default": _service(image, "grader", cpus=4.0, mem_limit="6g", volumes=["team:/team"])}
    for a in agents:
        services[a] = _service(image, a, cpus=AGENT_CPUS, mem_limit=AGENT_MEM, restart="unless-stopped",
                               **({"volumes": ["team:/team"]} if board else {}))
    path = BUILD / issue / f"compose-{len(agents)}{'-board' if board else ''}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp{os.getpid()}")
    tmp.write_text(yaml.safe_dump({"services": services, "volumes": {"team": {}}}, sort_keys=False))
    os.replace(tmp, path)
    return path


def _shell(ctx: ToolContext) -> ToolDef:
    inner = bash(timeout=TOOL_TIMEOUT_S, sandbox=ctx.agent_name)

    async def execute(command: str) -> str:
        return await inner(command=command)

    return ToolDef(execute, name="shell",
                   description=f"Run a bash command on your machine, starting in {WORKDIR}. Each call runs in a fresh "
                               f"shell and is stopped after {TOOL_TIMEOUT_S} seconds.",
                   parameters={"command": "The bash command to run."})


def _run_python(ctx: ToolContext) -> ToolDef:
    inner = python(timeout=TOOL_TIMEOUT_S, sandbox=ctx.agent_name)

    async def execute(code: str) -> str:
        return await inner(code=code)

    return ToolDef(execute, name="run_python",
                   description=f"Run Python 3 code on your machine, starting in {WORKDIR}. Print what you want to see. "
                               f"Stopped after {TOOL_TIMEOUT_S} seconds.",
                   parameters={"code": "The Python code to run."})


def tool_bindings() -> dict[str, ToolFactory]:
    """`shell` and `run_python` wrap Inspect's bash and python on the agent's own service."""
    return {"shell": ToolFactory(_shell), "run_python": ToolFactory(_run_python)}
