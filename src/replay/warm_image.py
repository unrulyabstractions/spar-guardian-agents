"""A run's image with Go's build cache filled from the base commit's packages, for a replay that must not fill a
cache per machine from cold: each agent's first build writes about a gigabyte, and several replays on one Docker disk
fill it, which turns calls into "no space left on device".

    tag = warm_image("cbswe-flipt-02e2:native")     # "cbswe-flipt-02e2:native-warm", built once per base image

The cache holds compiled packages only: the build runs `go test -count=1`, which keeps no test result, so a replayed
`go test` still runs its tests and prints them, its durations being masked anyway (output_match). The working tree is
checked unchanged by the build and the build has no network. What a warm cache can still change is what reads the
cache itself (du of ~/.cache, go clean, go build -x): the image is not the run's own, and replay.json says so beside
the base image's id. Opt-in: `python -m src replay --warm-image`.
"""
from __future__ import annotations

from src.sandbox.docker_images import docker, image_id, image_labels, recipe_sha256

LABEL = "replay.warm.recipe_sha256"
"""The label holding the sha256 of the recipe and the base image's id: a build happens when it differs."""
SUFFIX = "-warm"
RECIPE = """# {base} with Go's build cache filled (src/replay/warm_image.py)
FROM {base}
RUN cd {workdir} && git status --porcelain > /tmp/.before && (go build ./... ; go test -count=1 -run '^$' ./... \
>/dev/null 2>&1; true) \\
    && git status --porcelain > /tmp/.after && cmp /tmp/.before /tmp/.after && rm -f /tmp/.before /tmp/.after \\
    && du -sm /root/.cache/go-build
"""
"""The build: compile every package and every test binary of the tree, fail if the tree changed. The RUN line is the one
the bulk replay of 2026-11-19 built, so Docker's build cache can serve it."""


def warm_tag(base: str) -> str:
    """The derived image's tag: the base's tag with SUFFIX ("repo:native" -> "repo:native-warm")."""
    repo, _, tag = base.rpartition(":") if ":" in base.rsplit("/", 1)[-1] else (base, "", "latest")
    return f"{repo}:{tag}{SUFFIX}"


def recipe(base: str, workdir: str = "/app") -> bytes:
    return RECIPE.format(base=base, workdir=workdir).encode()


def warm_image(base: str, workdir: str = "/app", log=print) -> str:
    """The warm image of `base`, built (with no network) when missing or built from another recipe or base; returns
    its tag. RuntimeError when the base is not on this host or the build fails."""
    base_id = image_id(base)
    if not base_id:
        raise RuntimeError(f"the image {base} is not on this host")
    tag, body = warm_tag(base), recipe(base, workdir)
    sha = recipe_sha256(body, base_id.encode())
    if (image_labels(tag) or {}).get(LABEL) != sha:
        log(f"building {tag} from {base}", flush=True)
        done = docker("build", "--network", "none", "-t", tag, "--label", f"{LABEL}={sha}", "-", stdin=body,
                      check=False)
        if done.returncode != 0:
            raise RuntimeError(f"the warm build of {base} failed: {done.stderr.decode(errors='replace')[-1500:]}")
    return tag


def described(base: str, tag: str, recorded: str | None) -> dict:
    """replay.json's image for a replay on the warm image: the warm image, and the base the run used."""
    base_id, warm = image_id(base), image_id(tag)
    return {"tag": tag, "id": warm, "run_id": recorded, "own": False, "derived": "warm Go build cache",
            "base": {"tag": base, "id": base_id, "own": bool(base_id) and base_id == recorded}}
