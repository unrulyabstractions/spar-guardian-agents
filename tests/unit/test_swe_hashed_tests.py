"""Hashed hidden tests (swe_settings.Variant.hashed_tests): the grader's copy of the hidden tests holds no value of ours
in plain text, only its fingerprint, under a variant that exists only in the tests (tests/support/swe_hashed.py). With
the option off, ian_flipt's grader files are main's byte for byte. No Docker: tests/smoke/swe_hashed_tests.py runs the
Go tests."""
from __future__ import annotations

import dataclasses
import hashlib
import json
import re
import shlex

import pytest

from src.domains.swe import swe_inputs
from src.domains.swe.swe_inputs import (
    HASHED_LINES,
    fingerprint,
    grader_tests,
    load_split,
    owns,
    piece_patch,
    run_values,
    task_config,
    values_of,
)
from src.domains.swe.swe_settings import LONG_VALUES, OWN_VALUES, VARIANTS, Variant, long_random, own_values
from tests.support.swe_hashed import ISSUE, VARIANT

INSTANCE = load_split(ISSUE)["task"]
TEST_FILE = "internal/config/config_test.go"
MAIN_DIGESTS = {
    "config.json": "bfc5ae19e2e3f066f6967263a3dfe8e5f057a3b58b893cf10a20c9f182c518da",
    "parser.py": "fedec88f7bb3094f8c694a517fc4894d26cc508a5e932fd2099a0355dbcbd81a",
    "run_script.sh": "83374233a61a23ce2ef0c8353c008bbdd4c1f8e7f8ccc60241ce64da8e0323e0",
    "test.sh": "59b3d84478093557154357b4ab65bd5f7677f2e93d768f9fb805aa04ceb460ff",
}
"""The sha256 of each file grader_tests gave for flipt-02e2 on main at 0e660f3, before the option."""


def hashed_files() -> dict[str, bytes]:
    return grader_tests(INSTANCE, ISSUE, True, VARIANT)


def checkout_line(files: dict[str, bytes]) -> str:
    return json.loads(files["config.json"])["before_repo_set_cmd"].strip().splitlines()[-1]


def sed_apply(expression: str, text: str) -> str:
    """One of grader_tests' sed expressions applied as sed applies it: s|pattern|replacement|flags with each special
    character escaped, a pattern anchored at both ends replacing whole lines."""
    body, flags = expression[2:].rsplit("|", 1)
    pattern, replacement = re.split(r"(?<!\\)\|", body)
    plain = re.compile(r"\\(.)")
    replacement = plain.sub(r"\1", replacement)
    if pattern.startswith("^") and pattern.endswith("$"):
        line = plain.sub(r"\1", pattern[1:-1])
        return "\n".join(replacement if x == line else x for x in text.split("\n"))
    assert flags == "g", expression
    return text.replace(plain.sub(r"\1", pattern), replacement)


def sed_run(line: str, text: str) -> str:
    """The sed -i of a checkout line applied to one file's text."""
    words = shlex.split(line.split(" && ", 1)[1])
    assert words[:2] == ["sed", "-i"] and words[-1] == TEST_FILE, words
    for flag, expression in zip(words[2:-1:2], words[3:-1:2], strict=True):
        assert flag == "-e", words
        text = sed_apply(expression, text)
    return text


def stand_in() -> str:
    """A stand-in for the hidden test file: the lines the hashed edit anchors on, and the lines the fix adds to it."""
    patch = task_config(INSTANCE)["test_patch"]
    added = [x[1:] for x in dict(swe_inputs.file_blocks(patch))[TEST_FILE].splitlines()
             if x.startswith("+") and not x.startswith("+++")]
    assert_line = "\t\t\tassert.Equal(t, expected, res.Config)"
    return "\n".join(["package config", "import (", '\t"context"', '\t"strings"', ")",
                      "func TestLoad(t *testing.T) {", *added, assert_line, "\t\t\tx()", assert_line, "}"])


def test_hashed_grader_files_hold_no_value_and_hold_their_fingerprints():
    files = hashed_files()
    blob = b"".join(files.values())
    for value in own_values(ISSUE, VARIANT).values():
        assert value.encode() not in blob, value
        assert value.rsplit("-", 1)[1].split(".")[0].encode() not in blob, value
        assert blob.count(fingerprint(value).encode()) == 2, value   # the checkout's sed and the test_patch
    cfg = json.loads(files["config.json"])
    assert f'CaCertBytes = \\"{fingerprint(LONG_VALUES[ISSUE]["pemblock"])}\\\\n\\"' in json.dumps(cfg["test_patch"])
    assert {k: v for k, v in files.items() if k != "config.json"} == \
        {k: v for k, v in grader_tests(INSTANCE, ISSUE).items() if k != "config.json"}


def test_the_edit_is_applied_once_and_is_idempotent():
    files = hashed_files()
    line = checkout_line(files)
    assert line.count("sed -i") == 1 and line.count(" && ") == 1, line
    assert grader_tests(INSTANCE, ISSUE, True, VARIANT) == files
    text = stand_in()
    once = sed_run(line, text)
    assert sed_run(line, once) == once
    lines = once.split("\n")
    assert lines.count('\t"context"; "crypto/sha256"; "encoding/hex"') == 1
    assert sum(x.startswith("func hiddenTestFingerprints(") and x.endswith("; func TestLoad(t *testing.T) {")
               for x in lines) == 1
    assert lines.count("\t\t\tassert.Equal(t, expected, hiddenTestFingerprints(res.Config))") == 2
    assert "res.Config)" not in once.replace("hiddenTestFingerprints(res.Config))", "")
    for old, value in own_values(ISSUE, VARIANT).items():
        assert old not in once and value not in once and once.count(fingerprint(value)) == 1, old
    assert len(HASHED_LINES[ISSUE][TEST_FILE]) == 3


def test_a_fingerprint_keeps_trailing_newlines_out_of_the_hash():
    assert fingerprint("bundle-x\n") == fingerprint("bundle-x") == hashlib.sha256(b"bundle-x").hexdigest()


@pytest.mark.parametrize("values", [None, OWN_VALUES, {ISSUE: {"pemblock": "bundle-abcdefghijk"}},
                                    {ISSUE: {"pemblock": "bundle_abcdefghijklmn"}},
                                    {ISSUE: {"pemblock": "bundle-ABCDEFGHIJKLMN"}}])
def test_hashing_with_short_values_is_refused(values):
    with pytest.raises(ValueError, match="random part"):
        Variant(values=values, hashed_tests=True)
    with pytest.raises(ValueError, match="random part"):
        dataclasses.replace(VARIANTS["ian_flipt"], values=values, hashed_tests=True)


def test_long_values_take_the_shapes_of_the_issues_own():
    assert Variant(values=LONG_VALUES, hashed_tests=True).hashed_tests
    assert Variant(values=OWN_VALUES).values == OWN_VALUES        # short values without hashing stay allowed
    assert sorted(LONG_VALUES) == sorted(OWN_VALUES)
    for issue, found in LONG_VALUES.items():
        assert sorted(found) == sorted(OWN_VALUES[issue])
        for field, value in found.items():
            short = OWN_VALUES[issue][field]
            assert long_random(value) and not long_random(short), value
            assert re.sub(r"-[a-z0-9]+", "-", value) == re.sub(r"-[a-z0-9]+", "-", short), value


def test_the_option_is_off_for_every_task():
    assert not any(variant.hashed_tests or variant.values for variant in VARIANTS.values())


def test_with_the_option_off_ian_flipt_grader_files_are_mains():
    record = {"own_values": run_values(ISSUE, "jigsaw", VARIANTS["ian_flipt"])}
    for files in (grader_tests(INSTANCE, ISSUE), grader_tests(INSTANCE, ISSUE, True, VARIANTS["ian_flipt"]),
                  grader_tests(INSTANCE, ISSUE, owns(record))):
        assert {k: hashlib.sha256(v).hexdigest() for k, v in files.items()} == MAIN_DIGESTS


def test_every_reader_takes_the_variants_values_or_the_records():
    assert own_values(ISSUE) == own_values(ISSUE, VARIANTS["ian_flipt"]) == OWN_VALUES[ISSUE]
    assert own_values(ISSUE, VARIANT) == run_values(ISSUE, "jigsaw", VARIANT) == LONG_VALUES[ISSUE]
    assert run_values(ISSUE, "as_released", VARIANT) == {}
    record = {"own_values": LONG_VALUES[ISSUE]}
    assert values_of(ISSUE, owns(record)) == LONG_VALUES[ISSUE] and values_of(ISSUE, owns({})) == {}
    assert values_of(ISSUE, True, VARIANT) == LONG_VALUES[ISSUE] and values_of(ISSUE, False, VARIANT) == {}
    planted = "".join(piece_patch(ISSUE, part, owns(record)) for part in ("A", "B", "C"))
    for field, value in LONG_VALUES[ISSUE].items():
        assert planted.count(value) >= 1 and OWN_VALUES[ISSUE][field] not in planted, field
