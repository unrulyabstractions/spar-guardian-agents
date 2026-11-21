"""scripts/upload_runs_to_hf.py with no network: where the token comes from and that it is never shown, which files
go and which stay, what it refuses, that --dry-run uploads nothing, what --delete-stale deletes and what it never
touches, and the apart controls in their own layout."""
from __future__ import annotations

import hashlib
import zipfile
from types import SimpleNamespace

import pytest
from huggingface_hub import CommitOperationAdd, CommitOperationDelete, RepoFile
from huggingface_hub.errors import EntryNotFoundError

from scripts import pull_runs_from_hf as pull
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
                   runs / "run_b", logs / "run_a", logs / "screen", logs / "only_log",
                   logs / "set_aside_old" / "runs_run_b"):
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
    (logs / "set_aside_old" / "runs_run_b" / "run.json").write_text("{}")  # an old-layout copy
    return tmp_path


@pytest.fixture
def apart(tmp_path):
    base = tmp_path / "runs" / "apart_controls"
    run = base / "ds" / "binder" / "cheat" / "s1"
    (run / "eval").mkdir(parents=True)
    (run / "run.json").write_text("{}")
    (run / "eval" / "x.eval").write_bytes(b"e")
    (base / "ds" / ".DS_Store").write_bytes(b"\0")
    (base / "manifest.jsonl").write_text("{}\n")
    (base / "README.md").write_text("# apart\n")
    (base / ".DS_Store").write_bytes(b"\0")
    return tmp_path


class FakeApi:
    """Holds the Hub's files in memory: lists them, applies a commit's adds and deletes, and records each commit."""
    instances: list = []
    hub: dict[str, bytes] = {}

    def __init__(self, token=None, fail=None):
        self.token, self.fail, self.commits = token, fail, []
        FakeApi.instances.append(self)

    def list_repo_tree(self, repo_id, path_in_repo=None, *, recursive=False, repo_type=None):
        prefix = path_in_repo.rstrip("/") + "/"
        found = [RepoFile(path=path, size=len(data), oid=git_blob_id(data))
                 for path, data in sorted(FakeApi.hub.items()) if path.startswith(prefix)]
        if not found:
            raise EntryNotFoundError(f"404 {path_in_repo}")
        return found

    def create_commit(self, repo_id, repo_type, commit_message, operations):
        if self.fail:
            raise RuntimeError(self.fail)
        self.commits.append((repo_id, repo_type, commit_message, operations))
        for op in operations:
            if isinstance(op, CommitOperationAdd):
                with open(op.path_or_fileobj, "rb") as handle:
                    FakeApi.hub[op.path_in_repo] = handle.read()
            else:
                del FakeApi.hub[op.path_in_repo]
        return SimpleNamespace(oid=f"c{len(self.commits):039d}")


@pytest.fixture
def api(monkeypatch):
    FakeApi.instances, FakeApi.hub = [], {}
    monkeypatch.setattr(upload, "HfApi", FakeApi)
    return FakeApi


def commits(api) -> list:
    return [commit for instance in api.instances for commit in instance.commits]


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


def test_the_token_never_appears_in_any_output(root, api, monkeypatch, capsys, tmp_path):
    listing = tmp_path / "listing.tsv"
    assert upload.main(["study_x", "--run", "run_a", "--token", FAKE, "--dry-run", "--listing", str(listing)],
                       root=root) == 0
    assert upload.main(["study_x", "--run", "run_a", "--token", FAKE, "--delete-stale"], root=root) == 0
    monkeypatch.setenv("HF_TOKEN", FAKE)
    assert upload.main(["study_x", "--dry-run"], root=root) == 0
    out = capsys.readouterr()
    assert FAKE not in out.out + out.err + listing.read_text()
    assert "token    from --token" in out.out and "token    from HF_TOKEN" in out.out


def test_a_library_error_that_quotes_the_token_is_shown_without_it(root, api, monkeypatch, capsys):
    monkeypatch.setattr(upload, "HfApi", lambda token=None: FakeApi(token, fail=f"401 for Bearer {FAKE}"))
    with pytest.raises(SystemExit) as stop:
        upload.main(["study_x", "--run", "run_a", "--token", FAKE], root=root)
    assert stop.value.code == 2
    out = capsys.readouterr()
    assert FAKE not in out.out + out.err
    assert "the upload failed: RuntimeError: 401 for Bearer <token>; the commit may still land" in out.err


def test_a_listing_error_that_quotes_the_token_is_shown_without_it(root, monkeypatch, capsys):
    class Broken(FakeApi):
        def list_repo_tree(self, *args, **kwargs):
            raise RuntimeError(f"500 for Bearer {FAKE}")
    monkeypatch.setattr(upload, "HfApi", Broken)
    with pytest.raises(SystemExit):
        upload.main(["study_x", "--token", FAKE, "--dry-run"], root=root)
    out = capsys.readouterr()
    assert FAKE not in out.out + out.err
    assert "listing the Hub failed: RuntimeError: 500 for Bearer <token>" in out.err


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


def test_the_whole_study_takes_every_entry_but_smoke_set_aside_mac_files_and_eval_logs_no_run_names(root):
    items, skipped = upload.select("study_x", [], root)
    assert sorted(items) == ["INDEX.jsonl", "mock", "run_a", "run_b", "screen"]
    assert skipped == [".DS_Store", "only_log/b.eval", "run_a/0_journal.eval", "set_aside_old (logs/, old layout)",
                       "smoke"]
    every = [remote for files in items.values() for remote, _ in files]
    assert "study_x/runs/INDEX.jsonl" in every and "study_x/logs/run_a/a.eval" in every
    assert not any(r.endswith(("b.eval", "0_journal.eval")) for r in every)
    assert not any("smoke" in r or "set_aside" in r or ".DS_Store" in r or "/._" in r for r in every)


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
    assert commits(api) == []
    assert "files    5," in out and "dry run: nothing uploaded" in out and "add      5" in out
    assert any(line.split()[:3] == ["run_a", "4", "files"] for line in out.splitlines())
    assert any(line.split()[:3] == ["run_b", "1", "files"] for line in out.splitlines())


def test_dry_run_needs_no_token(root, api, capsys):
    assert upload.main(["study_x", "--dry-run"], root=root) == 0
    assert "none found; the real upload needs one" in capsys.readouterr().out
    assert commits(api) == [] and [instance.token for instance in api.instances] == [False]


def test_an_upload_is_one_commit_named_for_the_study_and_runs_and_is_checked_on_the_hub(root, api, capsys):
    assert upload.main(["study_x", "--run", "run_a run_b", "--token", FAKE], root=root) == 0
    [instance] = api.instances
    assert instance.token == FAKE
    [(repo, kind, message, operations)] = instance.commits
    assert (repo, kind, message) == ("unrulyabstractions/spar-swarm", "dataset",
                                     "study_x: run_a, run_b (5 added, 0 changed, 0 deleted)")
    assert len(operations) == 5
    out = capsys.readouterr().out
    assert "done     5 of 5 files on the Hub with the same bytes, 0 of 0 deleted" in out
    assert f"commit   c{1:039d}" in out
    assert "https://huggingface.co/datasets/unrulyabstractions/spar-swarm/tree/main/study_x" in out


def test_a_file_the_hub_does_not_hold_after_the_upload_fails_the_run(root, monkeypatch, capsys):
    FakeApi.instances, FakeApi.hub = [], {}

    class Lossy(FakeApi):
        def list_repo_tree(self, *args, **kwargs):
            return [e for e in super().list_repo_tree(*args, **kwargs) if not e.path.endswith(".eval")]
    monkeypatch.setattr(upload, "HfApi", Lossy)
    assert upload.main(["study_x", "--run", "run_a", "--token", FAKE], root=root) == 1
    assert "not on the Hub with the same bytes: study_x/logs/run_a/a.eval" in capsys.readouterr().out


def seed_hub(root) -> dict[str, bytes]:
    """A Hub that holds the whole study once, then: a changed file, a run gone here, a smoke file and a stray log that
    are here but left out, two set_aside_ copies (one named by a local file), and the study's own documents."""
    upload.main(["study_x", "--token", FAKE], root=root)
    hub = FakeApi.hub
    hub["study_x/runs/run_b/run.json"] = b'{"old": 1}'
    hub["study_x/runs/run_gone/run.json"] = b"{}"
    hub["study_x/logs/run_gone/g.eval"] = b"g"
    hub["study_x/logs/screen/run_gone.log"] = b"ran\n"
    hub["study_x/runs/smoke/s1/run.json"] = b"{}"
    hub["study_x/logs/only_log/b.eval"] = b"x"
    hub["study_x/logs/set_aside_old/runs_run_b/run.json"] = b"{}"
    hub["study_x/logs/set_aside_kept/x.json"] = b"{}"
    hub["study_x/README.md"] = b"# the study\n"
    hub["study_x/figures/f.png"] = b"png"
    (root / "logs" / "study_x" / "set_aside_kept").mkdir()
    (root / "logs" / "study_x" / "set_aside_kept" / "x.json").write_text("{}")
    (root / "runs" / "study_x" / "run_b" / "notes.json").write_text('{"see": "set_aside_kept/x.json"}')
    FakeApi.instances = []
    return dict(hub)


def test_compare_sorts_every_hub_file_in_reach_and_never_a_studys_own_documents(root, api):
    seed_hub(root)
    items, _ = upload.select("study_x", [], root)
    every = [f for files in items.values() for f in files]
    folders, single = upload.reach("study_x", [])
    hub = upload.hub_tree(FakeApi(), upload.REPO, "study_x", folders, single)
    assert "study_x/README.md" not in hub and "study_x/figures/f.png" not in hub
    ops = upload.compare(every, hub, "study_x", root, delete_stale=True)
    assert ops["add"] == ["study_x/runs/run_b/notes.json"]
    assert ops["change"] == ["study_x/runs/run_b/run.json"]
    assert len(ops["same"]) == len(every) - 2
    assert ops["delete"] == ["study_x/logs/run_gone/g.eval", "study_x/logs/screen/run_gone.log",
                             "study_x/logs/set_aside_old/runs_run_b/run.json", "study_x/runs/run_gone/run.json"]
    assert ops["keep"] == ["study_x/logs/only_log/b.eval", "study_x/logs/set_aside_kept/x.json",
                           "study_x/runs/smoke/s1/run.json"]
    assert ops["stale"] == []
    assert upload.compare(every, hub, "study_x", root, delete_stale=False)["stale"] == ops["delete"]


def test_without_delete_stale_nothing_on_the_hub_is_deleted(root, api, capsys):
    before = seed_hub(root)
    capsys.readouterr()
    assert upload.main(["study_x", "--token", FAKE], root=root) == 0
    [(_, _, message, operations)] = commits(api)
    assert not any(isinstance(op, CommitOperationDelete) for op in operations)
    assert message == "study_x: the whole study (1 added, 1 changed, 0 deleted)"
    assert set(before) <= set(FakeApi.hub)
    assert "note     4 stale Hub file(s) stay; --delete-stale deletes them" in capsys.readouterr().out


def test_delete_stale_deletes_in_the_same_commit_only_files_gone_here_and_unnamed_set_aside_copies(root, api, capsys):
    before = seed_hub(root)
    capsys.readouterr()
    assert upload.main(["study_x", "--token", FAKE, "--delete-stale"], root=root) == 0
    [(_, _, message, operations)] = commits(api)
    deleted = sorted(op.path_in_repo for op in operations if isinstance(op, CommitOperationDelete))
    added = sorted(op.path_in_repo for op in operations if isinstance(op, CommitOperationAdd))
    assert added == ["study_x/runs/run_b/notes.json", "study_x/runs/run_b/run.json"]
    assert deleted == ["study_x/logs/run_gone/g.eval", "study_x/logs/screen/run_gone.log",
                       "study_x/logs/set_aside_old/runs_run_b/run.json", "study_x/runs/run_gone/run.json"]
    assert message == "study_x: the whole study (1 added, 1 changed, 4 deleted)"
    for path in deleted:
        assert path not in FakeApi.hub
        if "set_aside" not in path:
            assert not upload.local_of(path, "study_x", root).exists()
    assert set(before) - set(deleted) <= set(FakeApi.hub)
    assert FakeApi.hub["study_x/README.md"] == b"# the study\n" and "study_x/figures/f.png" in FakeApi.hub
    out = capsys.readouterr().out
    assert "   delete study_x/runs/run_gone/run.json" in out
    assert "done     8 of 8 files on the Hub with the same bytes, 4 of 4 deleted" in out


def test_delete_stale_with_runs_reaches_only_those_runs_paths(root, api):
    seed_hub(root)
    FakeApi.hub["study_x/runs/run_a/stale.json"] = b"{}"
    FakeApi.hub["study_x/logs/screen/run_a.log"] = b"other\n"
    assert upload.main(["study_x", "--run", "run_a", "--token", FAKE, "--delete-stale"], root=root) == 0
    [(_, _, message, operations)] = commits(api)
    assert [op.path_in_repo for op in operations if isinstance(op, CommitOperationDelete)] == [
        "study_x/runs/run_a/stale.json"]
    assert [op.path_in_repo for op in operations if isinstance(op, CommitOperationAdd)] == [
        "study_x/logs/screen/run_a.log"]
    assert "study_x/runs/run_gone/run.json" in FakeApi.hub


def test_a_rerun_with_nothing_new_makes_no_commit(root, api, capsys):
    assert upload.main(["study_x", "--token", FAKE], root=root) == 0
    FakeApi.instances = []
    assert upload.main(["study_x", "--token", FAKE, "--delete-stale"], root=root) == 0
    assert commits(api) == []
    assert "nothing to commit" in capsys.readouterr().out


def test_the_listing_names_every_path_by_kind_with_both_sizes(root, api, tmp_path):
    seed_hub(root)
    listing = tmp_path / "out" / "listing.tsv"
    assert upload.main(["study_x", "--dry-run", "--delete-stale", "--listing", str(listing)], root=root) == 0
    assert commits(api) == []
    rows = [line.split("\t") for line in listing.read_text().splitlines()]
    assert rows[0] == ["kind", "path", "local_bytes", "hub_bytes"]
    by_path = {path: (kind, here, there) for kind, path, here, there in rows[1:]}
    assert by_path["study_x/runs/run_gone/run.json"] == ("delete", "", "2")
    assert by_path["study_x/runs/run_b/notes.json"][0::2] == ("add", "")
    assert by_path["study_x/runs/run_b/run.json"] == ("change", "2", "10")
    assert by_path["study_x/runs/smoke/s1/run.json"][0] == "keep"
    assert sum(1 for kind, *_ in rows[1:] if kind == "same") == 6


def test_the_apart_controls_go_to_their_own_layout(apart, api):
    items, skipped = upload.select("apart_controls", [], apart)
    assert sorted(items) == ["README.md", "ds", "manifest.jsonl"] and skipped == [".DS_Store"]
    assert [remote for remote, _ in items["ds"]] == ["apart_controls/ds/binder/cheat/s1/eval/x.eval",
                                                    "apart_controls/ds/binder/cheat/s1/run.json"]
    for run in ("ds/binder/cheat/s1", "apart_controls/ds/binder/cheat/s1/", "runs/apart_controls/ds/binder/cheat/s1"):
        one, _ = upload.select("apart_controls", [run], apart)
        assert [remote for files in one.values() for remote, _ in files] == [remote for remote, _ in items["ds"]]
    assert upload.reach("apart_controls", []) == (["apart_controls/"], [])
    assert upload.reach("apart_controls", ["ds/binder/cheat/s1"]) == (["apart_controls/ds/binder/cheat/s1/"], [])
    with pytest.raises(SystemExit):
        upload.select("apart_controls", ["ds/../../x"], apart)


def test_the_apart_controls_upload_deletes_only_what_is_gone_here(apart, api, capsys):
    FakeApi.hub.update({"apart_controls/ds/binder/cheat/s9/run.json": b"{}", "README.md": b"# dataset\n",
                        "study_x/runs/r/run.json": b"{}"})
    assert upload.main(["apart_controls", "--token", FAKE, "--delete-stale"], root=apart) == 0
    [(_, _, message, operations)] = commits(api)
    assert [op.path_in_repo for op in operations if isinstance(op, CommitOperationDelete)] == [
        "apart_controls/ds/binder/cheat/s9/run.json"]
    assert message == "apart_controls: the whole study (4 added, 0 changed, 1 deleted)"
    assert FakeApi.hub["README.md"] == b"# dataset\n" and "study_x/runs/r/run.json" in FakeApi.hub
    assert "done     4 of 4 files on the Hub with the same bytes, 1 of 1 deleted" in capsys.readouterr().out


def test_the_apart_controls_are_scanned_for_keys_like_a_study(apart, api, capsys):
    (apart / "runs" / "apart_controls" / "ds" / "binder" / "cheat" / "s1" / "run.json").write_text(
        '{"k": "sk-or-v1-fakefakefakefake0000"}')
    with pytest.raises(SystemExit):
        upload.main(["apart_controls", "--token", FAKE], root=apart)
    err = capsys.readouterr().err
    assert "run.json: holds OPENROUTER_API_KEY" in err and "sk-or-v1-fake" not in err
    assert api.instances == []


@pytest.mark.parametrize("remote", [
    "study_x/runs/run_a/run.json", "study_x/logs/run_a/a.eval", "study_x/logs/screen/run_a.log",
    "apart_controls/ds/binder/cheat/s1/run.json", "apart_controls/manifest.jsonl",
    "study_x/README.md", "study_x/figures/f.png", "README.md", "study_x/runs/../x",
])
def test_local_of_is_the_pulls_mapping(tmp_path, remote):
    assert upload.local_of(remote, remote.split("/")[0], tmp_path) == pull.local_path(remote, tmp_path)
