"""elicitation/publish.py with no network: what it refuses to publish, what it would publish, and how it compares the
Hub's files with the local ones."""
from __future__ import annotations

import hashlib
import subprocess
import sys
import zipfile
from types import SimpleNamespace

import huggingface_hub
import pytest

from elicitation import publish

KEY = "sk-or-v1-" + "0123456789abcdef" * 4


def study(tmp_path, name="study_x"):
    (tmp_path / "runs" / name / "run_a").mkdir(parents=True)
    (tmp_path / "logs" / name).mkdir(parents=True)
    (tmp_path / "runs" / name / "run_a" / "run.json").write_text('{"ok": true}')
    (tmp_path / "runs" / name / ".DS_Store").write_bytes(b"\0")
    with zipfile.ZipFile(tmp_path / "logs" / name / "a.eval", "w") as archive:
        archive.writestr("samples/1.json", '{"text": "clean"}')
    return tmp_path


def test_the_files_go_under_the_repository_prefix_and_the_mac_files_stay_home(tmp_path):
    files = publish.local_files("study_x", study(tmp_path))
    assert sorted(files) == ["spar-guardian-agents/logs/study_x/a.eval",
                             "spar-guardian-agents/runs/study_x/run_a/run.json"]


def test_a_key_is_found_in_a_file_and_inside_an_archive_and_never_printed(tmp_path):
    root = study(tmp_path)
    files = list(publish.local_files("study_x", root).values())
    assert publish.findings(files, {"SECRET_TOKEN": b"tok-1234567890abcdef"}) == []
    with zipfile.ZipFile(root / "logs" / "study_x" / "a.eval", "a") as archive:
        archive.writestr("samples/2.json", '{"h": "tok-1234567890abcdef"}')
    (root / "runs" / "study_x" / "run_a" / "run.json").write_text(f'{{"k": "{KEY}", "mail": "me@example.org"}}')
    values = {"SECRET_TOKEN": b"tok-1234567890abcdef", "the git user's email": b"me@example.org"}
    found = publish.findings(files, values)
    assert any(f.endswith("a.eval::samples/2.json: holds SECRET_TOKEN") for f in found)
    assert any(f.endswith("run.json: holds an OpenRouter key") for f in found)
    assert any(f.endswith("run.json: holds the git user's email") for f in found)
    assert not any("tok-1234567890abcdef" in f or KEY in f for f in found)


def test_a_key_in_a_zstandard_member_is_found_and_an_unreadable_member_blocks(tmp_path, monkeypatch):
    # Inspect writes its .eval members with Zstandard (method 93)
    path = tmp_path / "z.eval"
    with zipfile.ZipFile(path, "w", compression=93) as archive:
        archive.writestr("samples/1.json", '{"h": "tok-1234567890abcdef"}')
    assert [i.compress_type for i in zipfile.ZipFile(path).infolist()] == [93]
    # a short member may sit uncompressed in the archive's bytes too, so the member's own finding is what counts
    found = publish.findings([path], {"SECRET_TOKEN": b"tok-1234567890abcdef"})
    assert f"{path}::samples/1.json: holds SECRET_TOKEN" in found

    def unreadable(self, name, pwd=None):
        raise NotImplementedError("That compression method is not supported")
    monkeypatch.setattr(zipfile.ZipFile, "read", unreadable)
    assert publish.findings([path], {}) == [f"{path}::samples/1.json: cannot be read (NotImplementedError)"]


def test_a_fresh_interpreter_reads_the_zstandard_members_through_the_script_alone(tmp_path):
    # other test modules teach zipfile Zstandard in this process; `python -m elicitation.publish` starts clean
    path = tmp_path / "z.eval"
    with zipfile.ZipFile(path, "w", compression=93) as archive:
        archive.writestr("samples/1.json", '{"text": "clean"}')
    code = ("import sys; from elicitation import publish; "
            "print(publish.findings([publish.Path(sys.argv[1])], {}))")
    run = subprocess.run([sys.executable, "-c", code, str(path)], cwd=publish.settings.ROOT, capture_output=True,
                         text=True, timeout=120)
    assert run.returncode == 0 and run.stdout.strip() == "[]", run.stdout + run.stderr


def test_the_environment_keys_are_secrets(monkeypatch):
    monkeypatch.setenv("SOME_API_KEY", "abcdefghijklmnopqrstuvwxyz")
    monkeypatch.setenv("SHORT_TOKEN", "short")
    values = publish.secrets()
    assert values["SOME_API_KEY"] == b"abcdefghijklmnopqrstuvwxyz" and "SHORT_TOKEN" not in values


def test_the_git_blob_id_is_git_s(tmp_path):
    path = tmp_path / "f.txt"
    path.write_bytes(b"hello\nworld\n")
    expected = subprocess.run(["git", "hash-object", str(path)], capture_output=True, text=True).stdout.strip()
    assert publish.git_blob_id(path.read_bytes()) == expected


def test_a_hub_file_is_the_same_only_with_the_same_size_and_hash(tmp_path):
    path = tmp_path / "f.bin"
    path.write_bytes(b"abc" * 10)
    data = path.read_bytes()
    plain = SimpleNamespace(size=len(data), blob_id=publish.git_blob_id(data), lfs=None)
    in_lfs = SimpleNamespace(size=len(data), blob_id="x", lfs={"sha256": hashlib.sha256(data).hexdigest()})
    assert publish.same(path, plain) and publish.same(path, in_lfs)
    assert not publish.same(path, SimpleNamespace(size=len(data), blob_id="0" * 40, lfs=None))
    assert not publish.same(path, SimpleNamespace(size=len(data) + 1, blob_id=plain.blob_id, lfs=None))
    assert not publish.same(path, SimpleNamespace(size=len(data), blob_id="x", lfs={"sha256": "0" * 64}))
    local = {"a": path, "b": path}
    missing, different, extra = publish.compare(local, {"a": plain, "c": plain})
    assert (missing, different, extra) == (["b"], [], ["c"])


class FakeApi:
    def __init__(self, private=True):
        self.private, self.commits = private, []

    def dataset_info(self, repo):
        return SimpleNamespace(private=self.private)

    def create_commit(self, **kwargs):
        self.commits.append(kwargs)


@pytest.fixture
def fake_hub(monkeypatch, tmp_path):
    root = study(tmp_path)
    monkeypatch.setattr(publish.settings, "ROOT", root)
    monkeypatch.setattr(publish, "secrets", lambda: {"SECRET_TOKEN": b"tok-1234567890abcdef"})
    api = FakeApi()
    monkeypatch.setattr(huggingface_hub, "HfApi", lambda: api)
    return root, api


def test_a_public_dataset_is_refused_before_anything_is_read_or_sent(fake_hub, capsys):
    root, api = fake_hub
    api.private = False
    with pytest.raises(SystemExit) as stop:
        publish.main(["study_x"])
    assert stop.value.code == 2 and "not private" in capsys.readouterr().err and api.commits == []


def test_a_finding_is_refused_and_nothing_is_sent(fake_hub, capsys):
    root, api = fake_hub
    (root / "runs" / "study_x" / "run_a" / "run.json").write_text('{"k": "tok-1234567890abcdef"}')
    with pytest.raises(SystemExit) as stop:
        publish.main(["study_x"])
    err = capsys.readouterr().err
    assert stop.value.code == 2 and "holds SECRET_TOKEN" in err and "tok-1234567890abcdef" not in err
    assert api.commits == []


def test_a_dry_run_sends_nothing_and_a_study_with_no_file_is_refused(fake_hub):
    root, api = fake_hub
    assert publish.main(["study_x", "--dry-run"]) == 0 and api.commits == []
    with pytest.raises(SystemExit) as stop:
        publish.main(["study_none"])
    assert stop.value.code == 2


def test_a_publish_sends_every_file_in_one_commit_and_checks_the_hub(fake_hub, monkeypatch):
    root, api = fake_hub
    local = publish.local_files("study_x")
    hub = {name: SimpleNamespace(size=path.stat().st_size, blob_id=publish.git_blob_id(path.read_bytes()), lfs=None)
           for name, path in local.items()}
    monkeypatch.setattr(publish, "remote_files", lambda api_, name: hub)
    assert publish.main(["study_x"]) == 0
    [commit] = api.commits
    assert commit["repo_id"] == publish.REPO and commit["repo_type"] == "dataset"
    assert sorted(op.path_in_repo for op in commit["operations"]) == sorted(local)
    # a file the Hub holds with other bytes fails the run
    first = sorted(hub)[0]
    hub[first] = SimpleNamespace(size=1, blob_id="x", lfs=None)
    assert publish.main(["study_x"]) == 1


def test_a_chess_honeypot_credential_passes_and_any_other_bearer_token_blocks(tmp_path):
    root = study(tmp_path)
    halves = ["Abcdefghijklmnop", "qrstuvwxyz012345"]
    token = "".join(halves)
    (root / "runs" / "study_x" / "run_a" / "run.json").write_text(
        f'{{"chess": {{"credential": ["{halves[0]}", "{halves[1]}"]}}, "h": "Bearer {token}"}}')
    files = list(publish.local_files("study_x", root).values())
    allowed = publish.honeypot_credentials(files)
    assert allowed == {token.encode()}
    assert publish.findings(files, {}) != []
    assert publish.findings(files, {}, allowed) == []
    with zipfile.ZipFile(root / "logs" / "study_x" / "a.eval", "a") as archive:
        archive.writestr("samples/2.json", '{"h": "Bearer zzzzzzzzzzzzzzzzzzzzzzzz"}')
    found = publish.findings(files, {}, allowed)
    assert any(f.endswith("a.eval::samples/2.json: holds a bearer token") for f in found)
    assert not any("run.json" in f for f in found)
    # a honeypot credential that is also a key in the environment is still found by its value
    found = publish.findings(files, {"SECRET_TOKEN": token.encode()}, allowed)
    assert any(f.endswith("run.json: holds SECRET_TOKEN") for f in found)
