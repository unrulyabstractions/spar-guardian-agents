"""The opt-in remedies of a replay: drawn certificates swapped for the recorded ones (random_content.py), and the warm
Go build cache image (warm_image.py). The swap script runs here on a temporary tree with this host's python; the
image's build is stubbed. No Docker, no model call."""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from src.replay import random_content as rc
from src.replay import warm_image as wi
from src.replay.replay_report import ReplayReport, read_report, write_report
from src.replay.replay_sandbox import Exec

DRAWN = ["MIIDCTCCAfGgAwIBAgIUPPNl7o3iWfZR9+UNvht5we4e3dQwDQYJKoZIhvcNAQEL", "BQAwEzERMA8GA1UEAwwIcmVk", "Zg=="]
RECORDED = ["MIIDCTCCAfGgAwIBAgIUHQKBpgHsrmk+RVq2ppX4mrC9l54wDQYJKoZIhvcNAQEL", "BQAwEzERMA8GA1UEAwwIcmXX", "Zg=="]


def pem(lines: list[str], kind: str = "CERTIFICATE") -> str:
    return f"-----BEGIN {kind}-----\n" + "\n".join(lines) + f"\n-----END {kind}-----\n"


def test_the_recorded_output_gives_the_whole_block_then_its_long_lines():
    pairs = rc.output_pairs("subject=CN=x\n" + pem(RECORDED), "subject=CN=x\n" + pem(DRAWN))
    # the whole block first, then each long line that differs ("Zg==" is equal and short)
    assert pairs == [("\n".join(DRAWN), "\n".join(RECORDED)), (DRAWN[0], RECORDED[0]), (DRAWN[1], RECORDED[1])]
    assert rc.swapped(pem(DRAWN), pairs) == pem(RECORDED)


@pytest.mark.parametrize("replayed", [pem(DRAWN, "PRIVATE KEY"), pem(DRAWN[:2]), pem(DRAWN) + pem(DRAWN), "no pem"])
def test_a_block_of_another_shape_is_left_alone(replayed):
    assert rc.output_pairs(pem(RECORDED), replayed) == []


def stored_diff(path: str, body: str, indent: str = "") -> str:
    lines = body.splitlines()
    return (f"diff --git a/{path} b/{path}\nnew file mode 100644\n--- /dev/null\n+++ b/{path}\n"
            f"@@ -0,0 +1,{len(lines)} @@\n" + "".join(f"+{indent}{ln}\n" for ln in lines))


def test_the_stored_diffs_give_a_swap_for_a_file_nothing_printed(tmp_path):
    diffs = tmp_path / "diffs"
    diffs.mkdir()
    (diffs / "a01.diff").write_text(stored_diff("internal/config/testdata/ca.pem", pem(RECORDED)) +
                                    stored_diff("config/x.yml", "ca: |\n" + pem(RECORDED), "  "))
    stored = rc.stored_blocks(diffs, ["a01.diff", "a01.own.diff"])
    assert sorted(stored) == ["config/x.yml", "internal/config/testdata/ca.pem"]
    assert stored["internal/config/testdata/ca.pem"] == [("CERTIFICATE", RECORDED)]
    pairs = rc.stored_pairs(stored, {"internal/config/testdata/ca.pem": pem(DRAWN)})
    assert pairs == [("\n".join(DRAWN), "\n".join(RECORDED)), (DRAWN[0], RECORDED[0]), (DRAWN[1], RECORDED[1])]
    assert rc.stored_pairs(stored, {"internal/config/testdata/ca.pem": pem(RECORDED)}) == []


def test_the_swap_script_reaches_copies_keeps_times_and_skips_git(tmp_path):
    (tmp_path / "app" / ".git").mkdir(parents=True)
    files = {"app/ca.pem": pem(DRAWN), "app/conf.yml": "ca: |\n" + "".join(f"  {ln}\n" for ln in DRAWN),
             "app/.git/blob": pem(DRAWN), "app/other.txt": "nothing"}
    for name, text in files.items():
        (tmp_path / name).write_text(text)
    os.utime(tmp_path / "app/ca.pem", (1_000_000, 1_000_000))
    pairs = rc.output_pairs(pem(RECORDED), pem(DRAWN))
    done = subprocess.run([sys.executable, "-c", rc.SWAP_SCRIPT], capture_output=True, check=True,
                          input=json.dumps([pairs, [str(tmp_path / "app")], rc.SKIP_DIRS, rc.FILE_MAX]).encode())
    assert sorted(json.loads(done.stdout)) == sorted(str(tmp_path / n) for n in ("app/ca.pem", "app/conf.yml"))
    assert (tmp_path / "app/ca.pem").read_text() == pem(RECORDED)
    assert (tmp_path / "app/conf.yml").read_text() == "ca: |\n" + "".join(f"  {ln}\n" for ln in RECORDED)
    assert (tmp_path / "app/.git/blob").read_text() == pem(DRAWN)
    assert (tmp_path / "app/ca.pem").stat().st_mtime == 1_000_000


class FakeBox:
    """exec runs the in-machine scripts here, with /app mapped to `root`."""

    def __init__(self, root):
        self.root, self.calls = root, []

    def exec(self, service, command, *, stdin=None, timeout=1800):
        self.calls.append(command)
        if command == f"python3 -c {rc.shlex.quote(rc.READ_SCRIPT)}":
            script, data = rc.READ_SCRIPT.replace('"/app"', repr(str(self.root))), stdin
        elif command == f"python3 -c {rc.shlex.quote(rc.IN_REPOSITORY_SCRIPT)}":
            script, data = rc.IN_REPOSITORY_SCRIPT.replace('"/app"', repr(str(self.root))), stdin
        else:
            pairs, _, skip, size = json.loads(stdin)
            script, data = rc.SWAP_SCRIPT, json.dumps([pairs, [str(self.root)], skip, size]).encode()
        done = subprocess.run([sys.executable, "-c", script], input=data, capture_output=True)
        return Exec(done.returncode, done.stdout, done.stderr, 0.0)


def machine(tmp_path, planted: str = "nothing\n"):
    """An agent's /app: a git repository whose one commit holds `planted`, and the agent's diffs dir."""
    app = tmp_path / "app"
    (app / "t").mkdir(parents=True)
    (app / "planted.txt").write_text(planted)
    git = ["git", "-C", str(app), "-c", "user.name=x", "-c", "user.email=x@x"]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-qm", "base"], check=True)
    diffs = tmp_path / "diffs"
    diffs.mkdir()
    return app, diffs


OWN = {"a01": ["a01.diff", "a01.own.diff", "team.a01.diff"], "a02": ["a02.diff", "a02.own.diff", "team.a02.diff"]}
OPENSSL = "openssl req -x509 -out ca.pem && cat ca.pem"


def test_apply_swaps_the_machine_and_the_output_and_says_what(tmp_path):
    app, diffs = machine(tmp_path)
    (diffs / "a01.diff").write_text(stored_diff("t/key.pem", pem(RECORDED, "PRIVATE KEY")))
    (app / "t/key.pem").write_text(pem(DRAWN, "PRIVATE KEY"))
    (app / "ca.pem").write_text(pem(DRAWN))
    box, swap = FakeBox(app), rc.CertificateSwap(diffs, OWN, record=OPENSSL + "\n" + pem(RECORDED))
    output, record = swap.apply(box, "a01", pem(RECORDED), pem(DRAWN), OPENSSL)
    assert output == pem(RECORDED)
    assert record["source"] == ["output", "stored diffs"] and record["lines"] == 2
    assert (app / "ca.pem").read_text() == pem(RECORDED)
    assert (app / "t/key.pem").read_text() == pem(RECORDED, "PRIVATE KEY")
    # an exact output and a command naming no certificate run nothing in the machine
    box.calls.clear()
    assert swap.apply(box, "a01", "ok\n", "ok\n", "go test ./...") == ("ok\n", None) and box.calls == []


def test_the_report_keeps_the_options_and_the_swaps(tmp_path):
    write_report(tmp_path, ReplayReport(run="r", run_dir="runs/r", faithful=True,
                                        options={"pem": True, "warm_image": False},
                                        swaps=[{"n": 3, "agent": "a01", "k": 2, "lines": 2}]))
    report = read_report(tmp_path)
    assert report["options"] == {"pem": True, "warm_image": False} and report["swaps"][0]["n"] == 3


def test_the_warm_tag_and_recipe():
    assert wi.warm_tag("cbswe-flipt-02e2:native") == "cbswe-flipt-02e2:native-warm"
    assert wi.warm_tag("host:5000/x/y") == "host:5000/x/y:latest-warm"
    body = wi.recipe("cbswe-flipt-02e2:native").decode()
    assert "FROM cbswe-flipt-02e2:native\n" in body and "go test -count=1 -run '^$' ./..." in body
    assert "cmp /tmp/.before /tmp/.after" in body


def test_the_warm_image_is_built_once_per_recipe_and_base(monkeypatch):
    labels: dict = {}
    builds = []

    def docker(*args, stdin=None, check=True, timeout=7200):
        builds.append(args)
        labels.update(dict([args[args.index("--label") + 1].split("=", 1)]))
        return subprocess.CompletedProcess(args, 0, b"", b"")

    monkeypatch.setattr(wi, "image_id", lambda tag: "sha256:base")
    monkeypatch.setattr(wi, "image_labels", lambda tag: dict(labels))
    monkeypatch.setattr(wi, "docker", docker)
    assert wi.warm_image("img:native", log=lambda *a, **k: None) == "img:native-warm"
    assert builds[0][:3] == ("build", "--network", "none")
    wi.warm_image("img:native", log=lambda *a, **k: None)
    assert len(builds) == 1                                        # the label matches: no second build
    monkeypatch.setattr(wi, "image_id", lambda tag: "sha256:other-base")
    wi.warm_image("img:native", log=lambda *a, **k: None)
    assert len(builds) == 2                                        # another base: built again
    monkeypatch.setattr(wi, "image_id", lambda tag: None)
    with pytest.raises(RuntimeError, match="not on this host"):
        wi.warm_image("img:native")


def test_a_warm_replay_names_its_base(monkeypatch):
    monkeypatch.setattr(wi, "image_id", lambda tag: {"img:native": "sha256:a", "img:native-warm": "sha256:w"}[tag])
    assert wi.described("img:native", "img:native-warm", "sha256:a") == {
        "tag": "img:native-warm", "id": "sha256:w", "run_id": "sha256:a", "own": False,
        "derived": "warm Go build cache", "base": {"tag": "img:native", "id": "sha256:a", "own": True}}


def test_another_agents_diff_never_gives_a_swap(tmp_path):
    """The case of flipt_minimal_haiku_identified_fixed1: a02's stored fixture at the path a01 also writes is a02's
    work. a01's file at that path is left as the replay made it."""
    app, diffs = machine(tmp_path)
    (diffs / "a02.diff").write_text(stored_diff("t/ca.pem", pem(RECORDED)))
    (app / "t/ca.pem").write_text(pem(DRAWN))
    swap = rc.CertificateSwap(diffs, OWN, record=OPENSSL)
    assert swap.apply(FakeBox(app), "a01", "", "", OPENSSL) == ("", None)
    assert (app / "t/ca.pem").read_text() == pem(DRAWN)


def test_a_line_some_call_typed_or_showed_is_never_swapped(tmp_path):
    """A fake certificate written by hand in a heredoc is the same on every replay: the record holds its lines."""
    app, diffs = machine(tmp_path)
    (diffs / "a01.diff").write_text(stored_diff("t/ca.pem", pem(RECORDED)))
    (app / "t/ca.pem").write_text(pem(DRAWN))
    typed = f"cat > t/ca.pem <<'EOF'\n{pem(DRAWN)}EOF"
    swap = rc.CertificateSwap(diffs, OWN, record=typed + "\n" + pem(RECORDED))
    assert swap.apply(FakeBox(app), "a01", pem(RECORDED), pem(DRAWN), typed) == (pem(DRAWN), None)
    assert (app / "t/ca.pem").read_text() == pem(DRAWN)


def test_a_line_the_repository_holds_is_never_swapped(tmp_path):
    """A certificate checked out from a planted branch or the base is no draw, even where no call printed it."""
    app, diffs = machine(tmp_path, planted=pem(DRAWN))
    (diffs / "a01.diff").write_text(stored_diff("t/ca.pem", pem(RECORDED)))
    (app / "t/ca.pem").write_text(pem(DRAWN))
    swap = rc.CertificateSwap(diffs, OWN, record="git checkout origin/rd/x -- t/ca.pem")
    assert swap.apply(FakeBox(app), "a01", "", "", "git checkout origin/rd/x -- t/ca.pem && ls t/ca.pem") == ("", None)
    assert (app / "t/ca.pem").read_text() == pem(DRAWN)


def test_a_short_last_line_goes_with_its_block(tmp_path):
    """An ECDSA certificate's last base64 line is short (flipt_minimal_dsflash_fixed2's ca.pem ends "phLjy5NLiA=="):
    no line under LINE_MIN is swapped alone, but the block's whole body is, so the file ends equal to the recorded one.
    A block that differs only in short lines is no draw and stays."""
    drawn, recorded = [*DRAWN[:2], "+LJ2t8sxSA=="], [*RECORDED[:2], "phLjy5NLiA=="]
    app, diffs = machine(tmp_path)
    (diffs / "a01.diff").write_text(stored_diff("t/ca.pem", pem(recorded)))
    (app / "t/ca.pem").write_text(pem(drawn))
    (app / "copy.pem").write_text("x\n" + pem(drawn))
    swap = rc.CertificateSwap(diffs, OWN, record=OPENSSL)
    _, record = swap.apply(FakeBox(app), "a01", "", "", "go run /tmp/gencert.go > t/ca.pem && cp t/ca.pem copy.pem")
    assert (app / "t/ca.pem").read_text() == pem(recorded) and (app / "copy.pem").read_text() == "x\n" + pem(recorded)
    assert record["lines"] == 3
    assert rc.output_pairs(pem([*DRAWN[:2], "phLjy5NLiA=="]), pem([*DRAWN[:2], "+LJ2t8sxSA=="])) == []


def test_a_swap_keeps_the_drawn_lines_indent(tmp_path):
    """flipt_minimal_regulated_dsflash_sonnet_enforce_fill1: the agent wrote the drawn certificate into a YAML fixture
    at four spaces and re-indented it to six a call later. The stored fixture's lines carry the final six: the swap
    takes their content and keeps the four, so the later re-indent ends at six as recorded."""
    four = [" " * 4 + ln for ln in DRAWN]
    six = [" " * 6 + ln for ln in RECORDED]
    pairs = rc.line_pairs(rc.blocks(pem(four)), rc.blocks(pem(six)))
    assert pairs[0] == ("\n".join(four), "\n".join(" " * 4 + ln for ln in RECORDED))
    assert all(new.startswith("    ") and not new.startswith("     ") for _, new in pairs[1:])
