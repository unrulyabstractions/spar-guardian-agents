"""Stage one knowledge-work row inside the sandbox, as root, before any agent runs.

    python3 kw_setup.py /opt/stage

The stage directory holds contract/ (README.md template, brief.md, inputs/), artifacts/<pipeline deliverable>,
review/ (CheatBench's grader.py and review_runner.py) and layout.json, written by
src/benchmark/domains/knowledge/knowledge_sandbox.py. Three layouts:

  lone    CheatBench's: the user `user` owns /workspace, /data/cache and the pipeline artifact
  shared  a team: one Linux user per agent, one contract directory /workspace owned by root and the group `team`
          (setgid, group-writable, umask 002 for login shells)
  solo    a team: one Linux user per agent, each with its own contract copy /workspace/<name> (mode 700) and its own
          candidate id

On a team the pipeline artifact belongs to the group `pipeline`, whose members are the seats with `read`, and
evaluation.json and the review logs to the group `review`, whose members are the seats with `review`. When the
layout is split (some seat lacks an item) the artifacts directory is traverse-only, so a reader learns the
artifact's name only from a review holder. In every layout /external/contract_<id> is a symlink to a contract
directory, README.md names that path, and /opt/review (root, 700) holds the grader, the runner and a private copy of
the reference taken now.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def run(*cmd):
    subprocess.run(cmd, check=True)


def chown_tree(top, uid, gid, dir_mode, file_mode):
    for root, dirs, files in os.walk(top):
        os.chown(root, uid, gid)
        os.chmod(root, dir_mode)
        for f in files:
            p = os.path.join(root, f)
            os.chown(p, uid, gid)
            os.chmod(p, file_mode)


def gid_of(group):
    return int(subprocess.check_output(["getent", "group", group], text=True).split(":")[2])


def uid_of(user):
    return int(subprocess.check_output(["id", "-u", user], text=True))


def stage_contract(stage, dest, contract):
    shutil.copytree(stage / "contract", dest, dirs_exist_ok=True)
    (dest / "to_send").mkdir(exist_ok=True)
    (dest / "inputs").mkdir(exist_ok=True)
    readme = dest / "README.md"
    readme.write_text(readme.read_text().replace("{{contract_dir}}", contract + "/"))


def main(stage):
    stage = Path(stage)
    layout = json.loads((stage / "layout.json").read_text())
    kind = layout["layout"]
    seats = layout["seats"]
    external = Path(layout["external"])
    cache = Path(layout["cache"])
    artifacts = Path(layout["artifacts"])
    review = Path(layout["review_dir"])
    workdir = Path(layout["workdir"])

    if kind != "lone":
        run("groupadd", "--gid", str(layout["team_gid"]), layout["team_group"])
        run("groupadd", "--gid", str(layout["pipeline_gid"]), layout["pipeline_group"])
        run("groupadd", "--gid", str(layout["review_gid"]), layout["review_group"])
        for name, seat in seats.items():
            groups = [layout["team_group"]] + ([layout["pipeline_group"]] if seat["read"] else []) \
                + ([layout["review_group"]] if seat["review"] else [])
            run("useradd", "--uid", str(seat["uid"]), "--create-home", "--home-dir", seat["home"],
                "--shell", "/bin/bash", "--groups", ",".join(groups), seat["user"])
            os.chmod(seat["home"], 0o700)
        Path("/etc/profile.d/kw_umask.sh").write_text("umask 002\n")

    external.mkdir(parents=True, exist_ok=True)
    os.chmod(external, 0o755)
    contracts = {}
    for seat in seats.values():
        contracts.setdefault(seat["contract"], seat)
    if kind == "solo":
        os.chown(workdir, 0, 0)
        os.chmod(workdir, 0o711)
        os.chmod("/home", 0o711)
    for contract, seat in contracts.items():
        dest = Path(seat["root"])
        dest.mkdir(parents=True, exist_ok=True)
        stage_contract(stage, dest, contract)
        if kind == "lone":
            uid = uid_of(seat["user"])
            chown_tree(dest, uid, uid, 0o755, 0o644)
        elif kind == "shared":
            chown_tree(dest, 0, gid_of(layout["team_group"]), 0o2770, 0o660)
        else:
            uid = uid_of(seat["user"])
            chown_tree(dest, uid, uid, 0o755, 0o644)
            os.chmod(dest, 0o700)
        os.symlink(dest, external / Path(contract).name, target_is_directory=True)

    # the review record, the cache and the pipeline artifact
    artifacts.mkdir(parents=True, exist_ok=True)
    os.chmod(cache.parent, 0o755)
    comparator = Path(layout["comparator"])
    shutil.copy2(stage / "artifacts" / comparator.name, comparator)
    (cache / "evaluation.json").write_text(json.dumps(layout["evaluation"], indent=2) + "\n")
    if kind == "lone":
        uid = uid_of(next(iter(seats.values()))["user"])
        for p in (cache, artifacts):
            os.chown(p, uid, uid)
            os.chmod(p, 0o755)
        for p in (cache / "evaluation.json", comparator):
            os.chown(p, uid, uid)
            os.chmod(p, 0o644)
    else:
        team, pipeline = gid_of(layout["team_group"]), gid_of(layout["pipeline_group"])
        os.chown(cache, 0, team)
        os.chmod(cache, 0o2775)
        os.chown(cache / "evaluation.json", 0, gid_of(layout["review_group"]))
        os.chmod(cache / "evaluation.json", 0o640)
        os.chown(artifacts, 0, pipeline)
        os.chmod(artifacts, 0o2710 if layout["split"] else 0o2770)
        os.chown(comparator, 0, pipeline)
        os.chmod(comparator, 0o660)

    # the reviewer: root only, with the reference captured before any agent runs
    review.mkdir(parents=True, exist_ok=True)
    for name in ("grader.py", "review_runner.py"):
        shutil.copy2(stage / "review" / name, review / name)
    reference = review / "reference" / comparator.name
    reference.parent.mkdir(exist_ok=True)
    shutil.copy2(stage / "artifacts" / comparator.name, reference)
    meta = {"reference": str(reference), "comparator": str(comparator), "cache": str(cache),
            "log_group": layout["review_group"] if kind != "lone" else None,
            "seats": {name: {"root": seat["root"], "contract": seat["contract"], "user": seat["user"],
                              "candidate": seat["candidate"]} for name, seat in seats.items()}}
    (review / "meta.json").write_text(json.dumps(meta, indent=1))
    chown_tree(review, 0, 0, 0o700, 0o600)
    check = subprocess.run([sys.executable, str(review / "review_runner.py"), "check"], capture_output=True, text=True)
    if check.returncode != 0:
        raise SystemExit(f"the reference does not render: {check.stderr[-2000:]}")
    meta["reference_pages"] = json.loads(check.stdout)["pages"]
    (review / "meta.json").write_text(json.dumps(meta, indent=1))
    print(json.dumps({"layout": kind, "reference_pages": meta["reference_pages"], "contracts": sorted(contracts)}))


if __name__ == "__main__":
    main(sys.argv[1])
