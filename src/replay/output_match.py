"""How a replayed output compares with the recorded one, and what a difference most likely comes from.

    match_of(recorded, replayed)          # "exact", "masked" (equal once times and durations are masked), "order"
                                          # (equal as a set of lines once masked) or "differs"
    cause_of(recorded, replayed, cmd)     # for "differs": commit_hash, network, object_store, clock, environment,
                                          # numbers or other

The causes are the classes the pilot of 2026-11-19 named: filesystem enumeration order (the match class "order"), git's
object-store layout after the setup's gc, the wall clock, the host and the image, commit hashes and the network. Each
rule reads the texts alone, so a cause is a likely reading, never a proof. Pure.
"""
from __future__ import annotations

import re

MONTH = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
MASKS = [
    (re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?([+-]\d{2}:?\d{2}|Z)?"), "<TIME>"),
    (re.compile(rf"\b{MONTH}\s+\d{{1,2}}\s+(\d{{2}}:\d{{2}}(:\d{{2}})?|\d{{4}})"), "<TIME>"),
    (re.compile(r"\b\d{1,2}:\d{2}:\d{2}(\.\d+)?\b"), "<TIME>"),
    (re.compile(r"\b\d{10}(\.\d+)?\b"), "<TIME>"),
    (re.compile(r"\(cached\)|\b\d+(\.\d+)?(ns|µs|us|ms|s|m|h)\b(\d+(\.\d+)?s)?"), "<DUR>"),
    (re.compile(r"\breal\s+\S+|\buser\s+\d\S*|\bsys\s+\d\S*"), "<DUR>"),
]
"""Times and durations: an ISO or a log date, a clock time, an epoch, a test's or a build's duration, `time`'s
report."""
HEX = re.compile(r"\b[0-9a-f]{7,40}\b")
NUMBER = re.compile(r"\d+")
NETWORK = re.compile(r"\b(?:curl|wget|getent|ping|nslookup)\b|https?://|go (?:get|mod download)|proxy\.golang|"
                     r"resolve host|dial tcp", re.I)
OBJECT_STORE = re.compile(r"count-objects|\.git/objects|\bpack-|\bgc\b|\bfsck\b|du -|size-pack|verify-pack")
CLOCK = re.compile(r"--since|--until|--after|--before|\bdate\b|\bnow\b|\buptime\b|stat\s|-newer|-mtime|-mmin")
ENVIRONMENT = re.compile(r"/etc/|/proc/|\bps\b|\btop\b|\bls\s+-[a-zA-Z]*l[a-zA-Z]*\s+/(\s|$)|\buname\b|\bhostname\b|"
                         r"\benv\b|\bgo env\b|\bgo version\b|\bwhoami\b|\bid\b|\bdf\b|\bfree\b|nproc|telemetry")


def mask(text: str) -> str:
    for pattern, replacement in MASKS:
        text = pattern.sub(replacement, text)
    return text


def match_of(recorded: str, replayed: str) -> str:
    """exact, masked, order or differs."""
    if recorded == replayed:
        return "exact"
    a, b = mask(recorded), mask(replayed)
    if a == b:
        return "masked"
    if sorted(a.splitlines()) == sorted(b.splitlines()):
        return "order"
    return "differs"


def cause_of(recorded: str, replayed: str, command: str) -> str:
    """The likeliest cause of a difference: commit_hash (equal once ids are masked, in any line order), network,
    object_store, clock, environment (the host, the image, processes), numbers (equal once every number is masked:
    sizes, counts, process ids), else other."""
    a, b = HEX.sub("<H>", mask(recorded)), HEX.sub("<H>", mask(replayed))
    if a == b or sorted(a.splitlines()) == sorted(b.splitlines()):
        return "commit_hash"
    if NETWORK.search(command) or NETWORK.search(recorded):
        return "network"
    if OBJECT_STORE.search(command):
        return "object_store"
    if CLOCK.search(command):
        return "clock"
    if ENVIRONMENT.search(command):
        return "environment"
    return "numbers" if NUMBER.sub("<N>", a) == NUMBER.sub("<N>", b) else "other"
