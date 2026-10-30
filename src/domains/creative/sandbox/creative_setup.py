"""Stage one creative-writing row inside the sandbox, as root, before any agent runs.

    python3 creative_setup.py /opt/stage

The stage directory holds layout.json and, per workspace, instruction.txt, grade_config.json and grade.py (CheatBench's
grader as plain source), and the planted files, written by src/domains/creative/creative_sandbox.py. Per workspace the
setup writes instruction.txt and grading/ (grade.pyc compiled here with the name grade.py recorded, as _builder.py
build_pyc compiles it; grade_config.json; the compiled scorer under its interpreter's extension name; .review with the
endpoint's URL and the workspace's token, as CheatBench's host writes it), then gives the workspace its owner and
modes. It plants the reference, or the holders' segments, under /tmp/grading/ref, and waits until the review endpoint
answers. Three layouts:

  lone    CheatBench's: the user `user` owns the workspace and the planted reference (dirs 755, files 644)
  shared  a team: one Linux user per agent in the group `team`, which owns the one workspace (setgid, group-writable,
          umask 002 for login shells); the group only reads grading/
  solo    a team: one Linux user per agent, each owning its own workspace copy /workspace/<name> (700) and its own
          reference copy
"""
import json
import os
import py_compile
import shutil
import subprocess
import sys
import sysconfig
import time
import urllib.error
import urllib.request
from pathlib import Path


def run(*cmd):
    subprocess.run(cmd, check=True)


def ids(owner, group):
    """uid and gid of a user name and a group name ("root" is 0)."""
    uid = int(subprocess.check_output(["id", "-u", owner], text=True))
    gid = int(subprocess.check_output(["getent", "group", group], text=True).split(":")[2])
    return uid, gid


def own_tree(top, owner, group, dir_mode, file_mode):
    uid, gid = ids(owner, group)
    for root, dirs, files in os.walk(top):
        os.chown(root, uid, gid)
        os.chmod(root, dir_mode)
        for name in files:
            path = os.path.join(root, name)
            os.chown(path, uid, gid)
            os.chmod(path, file_mode)


def place(path, owner, group, mode):
    uid, gid = ids(owner, group)
    os.chown(path, uid, gid)
    os.chmod(path, mode)


def stage_workspace(stage, workdir, entry, layout, so_name):
    dest = Path(workdir)
    grading = dest / layout["grading"]
    grading.mkdir(parents=True, exist_ok=True)
    source = stage / entry["stage"]
    shutil.copy(source / "instruction.txt", dest / layout["brief"])
    shutil.copy(source / "grade_config.json", grading / "grade_config.json")
    py_compile.compile(str(source / "grade.py"), cfile=str(grading / "grade.pyc"), dfile="grade.py", doraise=True)
    shutil.copy(layout["score_so"], grading / so_name)
    (grading / ".review").write_text(json.dumps({"url": layout["review_url"], "token": entry["token"]}))
    own_tree(dest, entry["owner"], entry["group"], entry["dir_mode"], entry["file_mode"])
    own_tree(grading, entry["owner"], entry["group"], entry["grading_dir_mode"], entry["grading_file_mode"])
    os.chmod(dest, entry["top_mode"])


def wait_for_review(url, limit_s=60):
    """The endpoint answers an unauthorised request with 401 once it is up."""
    deadline = time.time() + limit_s
    while True:
        try:
            urllib.request.urlopen(urllib.request.Request(url, data=b"{}", method="POST"), timeout=5)
        except urllib.error.HTTPError as answer:
            if answer.code == 401:
                return
        except OSError:
            pass
        if time.time() > deadline:
            raise SystemExit(f"the review endpoint {url} never answered")
        time.sleep(0.5)


def main(stage):
    stage = Path(stage)
    layout = json.loads((stage / "layout.json").read_text())
    kind = layout["layout"]
    if kind != "lone":
        run("groupadd", "--gid", str(layout["team_gid"]), layout["team_group"])
        for seat in layout["seats"].values():
            run("useradd", "--uid", str(seat["uid"]), "--create-home", "--home-dir", seat["home"],
                "--shell", "/bin/bash", "--groups", layout["team_group"], seat["user"])
            os.chmod(seat["home"], 0o700)
    if kind == "shared":
        Path("/etc/profile.d/team_umask.sh").write_text("umask 002\n")
    if kind == "solo":
        os.chown(layout["workdir"], 0, 0)
        os.chmod(layout["workdir"], 0o711)
        os.chmod("/home", 0o711)
    so_name = "_score" + sysconfig.get_config_var("EXT_SUFFIX")
    for workdir, entry in layout["workspaces"].items():
        stage_workspace(stage, workdir, entry, layout, so_name)
    for entry in layout["plant_dirs"]:
        os.makedirs(entry["path"], exist_ok=True)
        place(entry["path"], entry["owner"], entry["group"], entry["mode"])
    for entry in layout["plants"]:
        shutil.copy(stage / entry["stage"], entry["path"])
        place(entry["path"], entry["owner"], entry["group"], entry["mode"])
    wait_for_review(layout["review_url"])
    print(json.dumps({"layout": kind, "workspaces": sorted(layout["workspaces"]),
                      "plants": [entry["path"] for entry in layout["plants"]], "score": so_name}))


if __name__ == "__main__":
    main(sys.argv[1])
