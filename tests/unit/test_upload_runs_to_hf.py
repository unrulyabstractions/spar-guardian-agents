"""scripts/upload_runs_to_hf.py with no network: where the token comes from and that it is never shown, which files
go and which stay, what it refuses, and that --dry-run uploads nothing."""
from __future__ import annotations

import hashlib
import zipfile

import pytest
from huggingface_hub import RepoFile

from scripts import upload_runs_to_hf as upload

FAKE = "hf_FAKEtokenFAKEtoken0123456789abcd"


def git_blob_id(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


@pytest.fixture(autouse=True)
def no_real_secrets(monkeypatch):
    """No test reads this shell's token, keys, saved login or git email."""
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.setattr(upload, "get_token", lambda: None)
    monkeypatch.setattr(upload, "secrets", lambda: {"OPENROUTER_API_KEY": b"sk-or-v1-fakefakefakefake0000"})


@pytest.fixture
def root(tmp_path):
    runs, logs = tmp_path / "runs" / "study_x", tmp_path / "logs" / "study_x"
    for folder in (runs / "run_a" / "trajectories" / "t#1@k7q2m", runs / "mock" / "run_m", runs / "smoke" / "s1",
                   runs / "run_b", logs / "run_a", logs / "screen", logs / "only_log"):
        folder.mkdir(parents=True)
    (runs / "run_a" / "run.json").write_text('{"eval_log": "logs/study_x/run_a/a.eval"}')
    (runs / "run_a" / "trajectories" / "t#1@k7q2m" / "transcript.json").write_text("[]")
    (runs / "run_a" / ".DS_Store").write_bytes(b"\0")
    (runs / "run_a" / "._run.json").write_bytes(b"\0")
    (runs / "mock" / "run_m" / "run.json").write_text("{}")
    (runs / "smoke" / "s1" / "run.json").write_text("{}")
    (runs / "run_b" / "run.json").write_text("{}")
    (runs / "INDEX.jsonl").write_text("{}\n")
    (runs / ".DS_Store").write_bytes(b"\0")
    with zipfile.ZipFile(logs / "run_a" / "a.eval", "w") as archive:
        archive.writestr("samples/1.json", '{"text": "clean"}')
    (logs / "screen" / "run_a.log").write_text("ran\n")
    (logs / "only_log" / "b.eval").write_bytes(b"x")                # an aborted start: no run names it
    (logs / "run_a" / "0_journal.eval").write_bytes(b"j")         # an aborted start beside the run's own log
    return tmp_path


class FakeApi:
    """Records what would go to the Hub and answers the listing with the bytes it was given."""
    instances: list = []

    def __init__(self, token=None, fail=None):
        self.token, self.fail, self.commits = token, fail, []
        FakeApi.instances.append(self)

    def create_commit(self, repo_id, repo_type, commit_message, operations):
        if self.fail:
            raise RuntimeError(self.fail)
        self.commits.append((repo_id, repo_type, commit_message, operations))

    def get_paths_info(self, repo_id, paths, repo_type):
        ops = {op.path_in_repo: op for _, _, _, operations in self.commits for op in operations}
        out = []
        for path in paths:
            if path in ops:
                with open(ops[path].path_or_fileobj, "rb") as handle:
                    data = handle.read()
                out.append(RepoFile(path=path, size=len(data), oid=git_blob_id(data)))
        return out


@pytest.fixture
def api(monkeypatch):
    FakeApi.instances = []
    monkeypatch.setattr(upload, "HfApi", FakeApi)
    return FakeApi


def test_the_token_comes_from_the_argument_then_the_environment_then_the_saved_login(monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "hf_from_env")
    monkeypatch.setattr(upload, "get_token", lambda: "hf_saved")
    assert upload.find_token("hf_given") == ("hf_given", "--token")
    assert upload.find_token(None) == ("hf_from_env", "HF_TOKEN")
    monkeypatch.delenv("HF_TOKEN")
    assert upload.find_token(None) == ("hf_saved", "the saved login (hf auth login)")
    monkeypatch.setattr(upload, "get_token", lambda: None)
    assert upload.find_token(None) == (None, None)


def test_no_token_stops_with_one_line_saying_how_to_give_one(root, api, capsys):
    with pytest.raises(SystemExit) as stop:
        upload.main(["study_x", "--run", "run_a"], root=root)
    assert stop.value.code == 2
    err = capsys.readouterr().err.strip().splitlines()
    assert err == [f"{upload.PROG}: error: {upload.NO_TOKEN}"]
    assert "hf auth login" in err[0] and "HF_TOKEN" in err[0] and "--token" in err[0]
    assert api.instances == []


def test_the_token_never_appears_in_any_output(root, api, monkeypatch, capsys):
    assert upload.main(["study_x", "--run", "run_a", "--token", FAKE, "--dry-run"], root=root) == 0
    assert upload.main(["study_x", "--run", "run_a", "--token", FAKE], root=root) == 0
    monkeypatch.setenv("HF_TOKEN", FAKE)
    assert upload.main(["study_x", "--dry-run"], root=root) == 0
    out = capsys.readouterr()
    assert FAKE not in out.out + out.err
    assert "token    from --token" in out.out and "token    from HF_TOKEN" in out.out


def test_a_library_error_that_quotes_the_token_is_shown_without_it(root, monkeypatch, capsys):
    monkeypatch.setattr(upload, "HfApi", lambda token=None: FakeApi(token, fail=f"401 for Bearer {FAKE}"))
    with pytest.raises(SystemExit) as stop:
        upload.main(["study_x", "--run", "run_a", "--token", FAKE], root=root)
    assert stop.value.code == 2
    out = capsys.readouterr()
    assert FAKE not in out.out + out.err
    assert "the upload failed: RuntimeError: 401 for Bearer <token>" in out.err


def test_a_file_that_holds_the_token_or_a_key_is_refused_and_named_without_the_value(root, api, capsys):
    (root / "runs" / "study_x" / "run_a" / "run.json").write_text(
        f'{{"leak": "{FAKE}", "eval_log": "logs/study_x/run_a/a.eval"}}')
    with zipfile.ZipFile(root / "logs" / "study_x" / "run_a" / "a.eval", "a") as archive:
        archive.writestr("samples/2.json", '{"k": "sk-or-v1-fakefakefakefake0000"}')
    with pytest.raises(SystemExit):
        upload.main(["study_x", "--run", "run_a", "--token", FAKE], root=root)
    out = capsys.readouterr()
    assert FAKE not in out.out + out.err and "sk-or-v1-fakefake" not in out.out + out.err
    assert "run.json: holds the Hugging Face token in use" in out.err
    assert "a.eval::samples/2.json: holds OPENROUTER_API_KEY" in out.err
    assert api.instances == []


def test_one_run_takes_its_folder_its_eval_logs_and_its_screen_log_and_leaves_the_mac_files(root):
    items, skipped = upload.select("study_x", ["run_a"], root)
    assert list(items) == ["run_a"] and skipped == ["run_a/0_journal.eval"]
    assert [remote for remote, _ in items["run_a"]] == [
        "study_x/runs/run_a/run.json",
        "study_x/runs/run_a/trajectories/t#1@k7q2m/transcript.json",
        "study_x/logs/run_a/a.eval",
        "study_x/logs/screen/run_a.log",
    ]
    nested, _ = upload.select("study_x", ["mock/run_m", "runs/study_x/run_b/"], root)
    assert [remote for files in nested.values() for remote, _ in files] == [
        "study_x/runs/mock/run_m/run.json", "study_x/runs/run_b/run.json"]


def test_the_whole_study_takes_every_entry_but_smoke_folders_mac_files_and_eval_logs_no_run_names(root):
    items, skipped = upload.select("study_x", [], root)
    assert sorted(items) == ["INDEX.jsonl", "mock", "run_a", "run_b", "screen"]
    assert skipped == [".DS_Store", "only_log/b.eval", "run_a/0_journal.eval", "smoke"]
    every = [remote for files in items.values() for remote, _ in files]
    assert "study_x/runs/INDEX.jsonl" in every and "study_x/logs/run_a/a.eval" in every
    assert not any(r.endswith(("b.eval", "0_journal.eval")) for r in every)
    assert not any("smoke" in r or ".DS_Store" in r or "/._" in r for r in every)


@pytest.mark.parametrize("study, runs, says", [
    ("study_none", [], "no study runs/study_none/ here; the studies here: study_x"),
    ("../study_x", [], "no study runs/../study_x/"),
    ("study_x", ["run_z"], "no run runs/study_x/run_z/"),
    ("study_x", ["../../logs/study_x/run_a"], "no run runs/study_x/../../logs/study_x/run_a/"),
    ("study_x", ["/etc"], "no run runs/study_x/etc/"),
])
def test_a_study_or_run_that_is_not_here_is_refused(root, api, capsys, study, runs, says):
    argv = [study, "--token", FAKE] + [arg for run in runs for arg in ("--run", run)]
    with pytest.raises(SystemExit) as stop:
        upload.main(argv, root=root)
    assert stop.value.code == 2
    assert says in capsys.readouterr().err
    assert api.instances == []


def test_dry_run_lists_each_run_and_uploads_nothing(root, api, capsys):
    assert upload.main(["study_x", "--run", "run_a", "--run", "run_b", "--token", FAKE, "--dry-run"], root=root) == 0
    out = capsys.readouterr().out
    assert api.instances == []
    assert "files    5," in out and "dry run: nothing uploaded" in out
    assert any(line.split()[:3] == ["run_a", "4", "files"] for line in out.splitlines())
    assert any(line.split()[:3] == ["run_b", "1", "files"] for line in out.splitlines())


def test_dry_run_needs_no_token(root, api, capsys):
    assert upload.main(["study_x", "--dry-run"], root=root) == 0
    assert "none found; the real upload needs one" in capsys.readouterr().out
    assert api.instances == []


def test_an_upload_is_one_commit_named_for_the_study_and_runs_and_is_checked_on_the_hub(root, api, capsys):
    assert upload.main(["study_x", "--run", "run_a run_b", "--token", FAKE], root=root) == 0
    [instance] = api.instances
    assert instance.token == FAKE
    [(repo, kind, message, operations)] = instance.commits
    assert (repo, kind, message) == ("unrulyabstractions/spar-swarm", "dataset", "study_x: run_a, run_b")
    assert len(operations) == 5
    out = capsys.readouterr().out
    assert "done     5 of 5 files on the Hub with the same bytes" in out
    assert "https://huggingface.co/datasets/unrulyabstractions/spar-swarm/tree/main/study_x" in out


def test_a_file_the_hub_does_not_hold_after_the_upload_fails_the_run(root, monkeypatch, capsys):
    class Lossy(FakeApi):
        def get_paths_info(self, repo_id, paths, repo_type):
            return [e for e in super().get_paths_info(repo_id, paths, repo_type) if not e.path.endswith(".eval")]
    monkeypatch.setattr(upload, "HfApi", Lossy)
    assert upload.main(["study_x", "--run", "run_a", "--token", FAKE], root=root) == 1
    assert "not on the Hub with the same bytes: study_x/logs/run_a/a.eval" in capsys.readouterr().out


def test_the_apart_controls_are_refused(tmp_path, capsys):
    """The dataset keeps the apart controls at apart_controls/<model>/..., not under a study's runs/."""
    (tmp_path / "runs" / "apart_controls" / "ds").mkdir(parents=True)
    with pytest.raises(SystemExit):
        upload.select("apart_controls", [], tmp_path)
    assert "studies only" in capsys.readouterr().err
