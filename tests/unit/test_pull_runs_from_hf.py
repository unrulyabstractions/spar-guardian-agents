"""scripts/hf/pull_runs_from_hf.py with no network: its mapping is the upload's reversed, a local file that differs
stops the pull without --force, --dry-run writes nothing, a study or run the Hub lacks is refused in one line, and a
pull places each file with the Hub's bytes."""
from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

import pytest
from huggingface_hub import RepoFile, RepoFolder

from scripts.hf import pull_runs_from_hf as pull
from scripts.hf import upload_runs_to_hf as upload
from tests.support.run_files import write_text

FAKE = "hf_FAKEtokenFAKEtoken0123456789abcd"
REPO = "unrulyabstractions/spar-swarm"


def git_blob_id(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


HUB = {  # the dataset: path -> bytes
    "README.md": b"# the dataset\n",
    "LICENSES/x.txt": b"licence\n",
    "study_x/README.md": b"# study x\n",
    "study_x/figures/rates.png": b"\x89PNG",
    "study_x/runs/INDEX.jsonl": b'{"path": "run_a", "valid": true}\n',
    "study_x/runs/run_a/processed/run.json": b'{"ok": true}',
    "study_x/runs/run_a/processed/trajectories/t#1@k7q2m/transcript.json": b"[]",
    "study_x/runs/run_ab/processed/run.json": b'{"other": 1}',
    "study_x/runs/mock/run_m/processed/run.json": b"{}",
    "study_x/logs/run_a/a.eval": b"PK eval bytes",
    "study_x/logs/screen/run_a.log": b"ran\n",
    "apart_controls/README.md": b"# apart\n",
    "apart_controls/runs/manifest.jsonl": b'{"key": "binder_cheat_ds_s1"}\n',
    "apart_controls/runs/ds/binder/cheat/s1/processed/run.json": b'{"seed": 1}',
    "apart_controls/logs/ds/binder/cheat/s1/x.eval": b"PK apart eval",
    "apart_controls/runs/ds/binder/cheat/s2/processed/run.json": b'{"seed": 2}',
}


def entry(path: str) -> RepoFile:
    data = HUB[path]
    return RepoFile(path=path, size=len(data), oid=git_blob_id(data))


class FakeApi:
    """Answers the Hub's listing from HUB, as the library does: a folder by RepoFolder, a missing path by nothing."""
    instances: list = []

    def __init__(self, token=None):
        self.token = token
        FakeApi.instances.append(self)

    @staticmethod
    def folders() -> set[str]:
        return {"/".join(p.split("/")[:i]) for p in HUB for i in range(1, p.count("/") + 1)}

    def get_paths_info(self, repo_id, paths, repo_type):
        assert (repo_id, repo_type) == (REPO, "dataset")
        return [entry(p) if p in HUB else RepoFolder(path=p, oid="tree") for p in paths
                if p in HUB or p in self.folders()]

    def list_repo_tree(self, repo_id, path_in_repo=None, recursive=False, repo_type=None):
        if path_in_repo is None:
            return [RepoFolder(path=f, oid="tree") for f in sorted(self.folders()) if "/" not in f] + [
                entry(p) for p in HUB if "/" not in p]
        assert recursive
        return [entry(p) for p in sorted(HUB) if p.startswith(path_in_repo + "/")]


@pytest.fixture(autouse=True)
def fake_hub(monkeypatch, tmp_path_factory):
    """No test reads this shell's token or reaches the network; the download writes HUB's files into a snapshot."""
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.setattr(upload, "get_token", lambda: None)
    FakeApi.instances = []
    monkeypatch.setattr(pull, "HfApi", FakeApi)
    calls = []

    def download(repo_id, repo_type, allow_patterns, cache_dir, token):
        import fnmatch
        calls.append({"patterns": allow_patterns, "token": token})
        snapshot = tmp_path_factory.mktemp("cache") / "snapshot"
        for path, data in HUB.items():
            if any(fnmatch.fnmatch(path, p) for p in allow_patterns):
                (snapshot / path).parent.mkdir(parents=True, exist_ok=True)
                (snapshot / path).write_bytes(data)
        return str(snapshot)

    monkeypatch.setattr(pull, "snapshot_download", download)
    return calls


def tree(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def test_the_mapping_is_the_uploads_reversed(tmp_path):
    root = tmp_path / "repo"
    runs, logs = root / "runs" / "study_x", root / "logs" / "study_x"
    for folder in (runs / "run_a" / "processed/trajectories" / "t#1@k7q2m", runs / "mock" / "run_m", logs / "run_a",
                   logs / "screen", logs / "only_log"):
        folder.mkdir(parents=True)
    (runs / "INDEX.jsonl").write_text("{}\n")
    write_text(runs / "run_a" / "processed/run.json", '{"eval_log": "logs/study_x/run_a/a.eval"}')
    (runs / "run_a" / "processed/trajectories" / "t#1@k7q2m" / "transcript.json").write_text("[]")
    write_text(runs / "mock" / "run_m" / "processed/run.json", "{}")
    with zipfile.ZipFile(logs / "run_a" / "a.eval", "w") as archive:
        archive.writestr("samples/1.json", "{}")
    (logs / "screen" / "run_a.log").write_text("ran\n")
    (logs / "only_log" / "b.eval").write_bytes(b"x")              # no run names it: it never goes up
    pairs = [pair for selection in (upload.select("study_x", ["run_a"], root)[0],
                                    upload.select("study_x", ["mock/run_m"], root)[0],
                                    upload.select("study_x", [], root)[0])
             for files in selection.values() for pair in files]
    assert len(pairs) == 11 and not any(remote.endswith("b.eval") for remote, _ in pairs)
    for remote, local in pairs:
        assert pull.local_path(remote, root) == local
    one_run = [remote for remote, _ in upload.select("study_x", ["run_a"], root)[0]["run_a"]]
    paths = pull.run_paths("study_x", "run_a")
    assert all(any(r == p or r.startswith(p + "/") for p in paths) for r in one_run)


def test_the_apart_controls_come_as_a_study_does(tmp_path):
    assert pull.local_path("apart_controls/logs/ds/binder/cheat/s1/x.eval", tmp_path) == \
        tmp_path / "logs" / "apart_controls" / "ds" / "binder" / "cheat" / "s1" / "x.eval"
    assert pull.local_path("apart_controls/runs/manifest.jsonl", tmp_path) == \
        tmp_path / "runs/apart_controls/manifest.jsonl"
    assert pull.run_paths("apart_controls", "ds/binder/cheat/s1")[:2] == [
        "apart_controls/runs/ds/binder/cheat/s1", "apart_controls/logs/ds/binder/cheat/s1"]


@pytest.mark.parametrize("remote", ["study_x/README.md", "study_x/figures/rates.png", "README.md", "LICENSES/x.txt",
                                    "study_x/runs/../../etc/passwd", "study_x/logs//a"])
def test_a_studys_own_documents_and_odd_paths_have_no_place_here(tmp_path, remote):
    assert pull.local_path(remote, tmp_path) is None


def test_a_run_pull_places_its_files_and_its_index_with_the_hubs_bytes(tmp_path, fake_hub, capsys):
    assert pull.main(["study_x", "--run", "run_a"], root=tmp_path) == 0
    assert tree(tmp_path) == {
        "logs/study_x/run_a/a.eval": HUB["study_x/logs/run_a/a.eval"],
        "logs/study_x/screen/run_a.log": HUB["study_x/logs/screen/run_a.log"],
        "runs/study_x/INDEX.jsonl": HUB["study_x/runs/INDEX.jsonl"],
        "runs/study_x/run_a/processed/run.json": HUB["study_x/runs/run_a/processed/run.json"],
        "runs/study_x/run_a/processed/trajectories/t#1@k7q2m/transcript.json":
            HUB["study_x/runs/run_a/processed/trajectories/t#1@k7q2m/transcript.json"],
    }
    [call] = fake_hub
    assert call["token"] is False and "study_x/runs/run_ab/processed/run.json" not in str(call["patterns"])
    out = capsys.readouterr().out
    assert "token        none (the dataset is public)" in out
    assert "index        study_x/runs/INDEX.jsonl: comes too" in out
    assert "5 of 5 files placed with the Hub's bytes" in out
    assert pull.main(["study_x", "--run", "run_a"], root=tmp_path) == 0
    assert "nothing to pull" in capsys.readouterr().out and len(fake_hub) == 1


def test_a_whole_study_leaves_its_documents_on_the_hub(tmp_path, capsys):
    assert pull.main(["study_x"], root=tmp_path) == 0
    assert sorted(tree(tmp_path)) == [
        "logs/study_x/run_a/a.eval", "logs/study_x/screen/run_a.log", "runs/study_x/INDEX.jsonl",
        "runs/study_x/mock/run_m/processed/run.json", "runs/study_x/run_a/processed/run.json",
        "runs/study_x/run_a/processed/trajectories/t#1@k7q2m/transcript.json", "runs/study_x/run_ab/processed/run.json"]
    assert "hub only     README.md, figures/" in capsys.readouterr().out


def test_an_apart_control_run_comes_with_the_manifest(tmp_path):
    assert pull.main(["apart_controls", "--run", "ds/binder/cheat/s1"], root=tmp_path) == 0
    assert tree(tmp_path) == {
        "logs/apart_controls/ds/binder/cheat/s1/x.eval": HUB["apart_controls/logs/ds/binder/cheat/s1/x.eval"],
        "runs/apart_controls/ds/binder/cheat/s1/processed/run.json":
            HUB["apart_controls/runs/ds/binder/cheat/s1/processed/run.json"],
        "runs/apart_controls/manifest.jsonl": HUB["apart_controls/runs/manifest.jsonl"],
    }


def test_a_local_file_that_differs_stops_the_pull_before_any_download(tmp_path, fake_hub, capsys):
    mine = tmp_path / "runs" / "study_x" / "run_a" / "processed/run.json"
    mine.parent.mkdir(parents=True)
    mine.write_text('{"mine": true}')
    with pytest.raises(SystemExit) as stop:
        pull.main(["study_x", "--run", "run_a"], root=tmp_path)
    assert stop.value.code == 2
    err = capsys.readouterr().err
    assert f"differs here: {mine}" in err
    assert "1 local file(s) differ from the Hub's; nothing was pulled; --force replaces them" in err
    assert fake_hub == [] and tree(tmp_path) == {"runs/study_x/run_a/processed/run.json": b'{"mine": true}'}
    assert pull.main(["study_x", "--run", "run_a", "--force"], root=tmp_path) == 0
    assert mine.read_bytes() == HUB["study_x/runs/run_a/processed/run.json"]


def test_a_differing_index_is_left_as_it_is_even_with_force(tmp_path, capsys):
    index = tmp_path / "runs" / "study_x" / "INDEX.jsonl"
    index.parent.mkdir(parents=True)
    index.write_text("mine\n")
    assert pull.main(["study_x", "--run", "run_a", "--force"], root=tmp_path) == 0
    assert index.read_text() == "mine\n"
    assert "here already, left as it is (it differs from the Hub's)" in capsys.readouterr().out


def test_dry_run_lists_each_run_and_writes_nothing(tmp_path, fake_hub, capsys):
    mine = tmp_path / "runs" / "study_x" / "run_a" / "processed/run.json"
    mine.parent.mkdir(parents=True)
    mine.write_text('{"mine": true}')
    before = tree(tmp_path)
    assert pull.main(["study_x", "--run", "run_a run_ab", "--dry-run"], root=tmp_path) == 0
    assert pull.main(["study_x", "--dry-run", "--force"], root=tmp_path) == 0
    assert tree(tmp_path) == before and fake_hub == []
    assert sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*")) == [
        "runs", "runs/study_x", "runs/study_x/run_a", "runs/study_x/run_a/processed",
        "runs/study_x/run_a/processed/run.json"]
    out = capsys.readouterr().out
    assert any(line.split()[:3] == ["run_a", "4", "files"] and "differs 1" in line for line in out.splitlines())
    assert any(line.split()[:3] == ["run_ab", "1", "files"] and "new 1" in line for line in out.splitlines())
    assert "a pull would stop at the 1 differing file(s)" in out
    assert "dry run: nothing written; a pull would write 7 file(s)" in out


@pytest.mark.parametrize("argv, says", [
    (["study_none"], f"no study study_none on the Hub in {REPO}; the studies there: apart_controls, study_x"),
    (["study_x", "--run", "run_z"], "no run run_z in study_x on the Hub"),
    (["study_x", "--run", "run"], "no run run in study_x on the Hub"),
    (["apart_controls", "--run", "ds/binder/cheat/s9"], "no run ds/binder/cheat/s9 in apart_controls on the Hub"),
    (["study_x", "--run", "../logs/run_a"], "no run ../logs/run_a in study_x"),
    (["../study_x"], "no study ../study_x"),
])
def test_a_study_or_run_the_hub_lacks_is_refused_in_one_line(tmp_path, fake_hub, capsys, argv, says):
    with pytest.raises(SystemExit) as stop:
        pull.main(argv, root=tmp_path)
    assert stop.value.code == 2
    err = capsys.readouterr().err.strip().splitlines()
    assert len(err) == 1 and err[0].startswith(f"{pull.PROG}: error: ") and says in err[0]
    assert fake_hub == [] and tree(tmp_path) == {}


def test_a_given_token_is_used_and_never_shown(tmp_path, fake_hub, capsys):
    assert pull.main(["study_x", "--run", "run_a", "--token", FAKE], root=tmp_path) == 0
    assert [api.token for api in FakeApi.instances] == [FAKE] and fake_hub[0]["token"] == FAKE
    out = capsys.readouterr()
    assert FAKE not in out.out + out.err and "token        from --token" in out.out


def test_a_download_error_that_quotes_the_token_is_shown_without_it(tmp_path, monkeypatch, capsys):
    def fail(**kwargs):
        raise RuntimeError(f"401 for Bearer {FAKE}")
    monkeypatch.setattr(pull, "snapshot_download", lambda *a, **k: fail(**k))
    with pytest.raises(SystemExit):
        pull.main(["study_x", "--run", "run_a", "--token", FAKE], root=tmp_path)
    out = capsys.readouterr()
    assert FAKE not in out.out + out.err
    assert "the download failed: RuntimeError: 401 for Bearer <token>" in out.err


def test_many_files_are_asked_for_by_run_folder(monkeypatch):
    monkeypatch.setattr(pull, "EXACT_PATTERNS", 2)
    needed = ["study_x/runs/run_a/processed/run.json", "study_x/runs/run_a/t#1/x.json", "study_x/logs/run_a/a.eval",
              "study_x/runs/INDEX.jsonl"]
    assert pull.patterns(needed, "study_x") == ["study_x/logs/run_a/*", "study_x/runs/INDEX.jsonl",
                                                "study_x/runs/run_a/*"]
    assert pull.patterns(needed[:2], "study_x") == ["study_x/runs/run_a/processed/run.json",
                                                    "study_x/runs/run_a/t#1/x.json"]
    assert pull.patterns(["study_x/runs/a[1]/processed/run.json"],
                         "study_x") == ["study_x/runs/a[[]1]/processed/run.json"]
