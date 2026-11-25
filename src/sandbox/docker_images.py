"""Docker images on the host: the id and labels of an image, and building one from a recipe on stdin when the label
no longer names the recipe.

    image = ensure_labelled_image("cbtask:python311", dockerfile, "cbtask.recipe_sha256", what="machines")

A recipe is a Dockerfile that copies nothing, so it builds with an empty context from stdin. Each image records the
sha256 of its recipe (and any salt, such as the id of the image it builds on) in a label; a build happens when the
image is missing or the label differs. The SWE domain builds with a context directory and uses the helpers below.
"""
from __future__ import annotations

import hashlib
import json
import subprocess


class DockerFailed(subprocess.CalledProcessError):
    """A docker command that failed, its message ending with docker's own stderr. Exit 125 is docker's, not the
    container's: the daemon refused the run (on 2026-11-22 three SWE starts got it from a full disk in Colima's VM,
    "no space left on device", which the bare exit status hid)."""

    def __str__(self) -> str:
        err = self.stderr.decode(errors="replace") if isinstance(self.stderr, bytes) else (self.stderr or "")
        return f"{super().__str__()} docker said: {err.strip()[-800:] or 'nothing'}"


def docker(*args: str, stdin: bytes | None = None, check: bool = True,
           timeout: int = 7200) -> subprocess.CompletedProcess:
    """One docker command, its output captured. With check, a failure raises DockerFailed, which names docker's
    stderr."""
    found = subprocess.run(["docker", *args], capture_output=True, timeout=timeout, input=stdin)
    if check and found.returncode != 0:
        raise DockerFailed(found.returncode, found.args, found.stdout, found.stderr)
    return found


def image_id(tag: str) -> str | None:
    """The image's id, or None when no image has the tag."""
    r = docker("image", "inspect", "--format", "{{.Id}}", tag, check=False)
    return r.stdout.decode().strip() if r.returncode == 0 else None


def image_labels(tag: str) -> dict | None:
    """The image's labels, or None when no image has the tag."""
    r = docker("image", "inspect", "--format", "{{json .Config.Labels}}", tag, check=False)
    return (json.loads(r.stdout.decode() or "null") or {}) if r.returncode == 0 else None


def recipe_sha256(recipe: bytes, salt: bytes = b"") -> str:
    return hashlib.sha256(recipe + salt).hexdigest()


def build_from_stdin(tag: str, dockerfile: bytes, label: str, value: str) -> None:
    """docker build from a Dockerfile on stdin with an empty context, labelled."""
    docker("build", "-t", tag, "--label", f"{label}={value}", "-", stdin=dockerfile)


def ensure_labelled_image(tag: str, recipe: bytes, label: str, *, salt: bytes = b"", what: str = "",
                          log=print) -> str | None:
    """Build `tag` from `recipe` when it is missing or its `label` is not the sha256 of recipe plus salt; return the
    image id. `what` names the builder in the log line."""
    sha = recipe_sha256(recipe, salt)
    if (image_labels(tag) or {}).get(label) != sha:
        log(f"{what or tag}: building {tag}")
        build_from_stdin(tag, recipe, label, sha)
    return image_id(tag)
