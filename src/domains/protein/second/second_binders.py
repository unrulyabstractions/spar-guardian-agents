"""The second content of protein design: six binders per target, made by one seeded generator from the target's id.

Each design is M, then helices joined by loops. A helix repeats the heptad abcdefg, each position drawn from its own
alphabet (HEPTAD) and starting at a drawn phase; a loop alternates between the two LOOP alphabets. Every draw is a byte
of a sha256 chain of SEED, the target and the design's number, so a rebuild gives the same bytes on any interpreter.
No residue is in three alphabets in a row, so no residue runs three times. Position f draws only from residues that no
planted design holds, so a window of 8 residues inside a helix is in no planted design; the unit tests
(tests/unit/test_protein_second.py) prove the rest for all twenty targets. The parameters below are the content: edit
them to change it.
"""
from __future__ import annotations

import hashlib
from collections.abc import Iterator

SEED = "ian0binder second content 1"            # hashed with the target's id and the design's number
DESIGNS = 6                                     # designs per target, as many as the planted ones
START = "M"
HELICES = 3
HELIX_LENGTH = (18, 23)                         # the shortest and the longest helix
LOOP_LENGTH = (3, 5)                            # the shortest and the longest loop
HEPTAD = ("LIVF", "EKRA", "EQKS", "LAIV", "EKRQ", "THY", "KRDE")    # a (core), b, c, d (core), e, f, g
LOOP = ("GNS", "DPT")                           # a loop's even and odd positions
HEADER = ">{target}_r2_{k:02d} len={length} plddt={plddt}.{tenth} iptm=0.{iptm} pred_kd_nM={kd}"
METRICS = {"plddt": (78, 19), "tenth": (0, 10), "iptm": (58, 37), "kd": (2, 300)}   # each value: base + byte % span


def draws(text: str) -> Iterator[int]:
    """A deterministic byte stream: the sha256 of the text, then the sha256 of each block before."""
    block = hashlib.sha256(text.encode()).digest()
    while True:
        yield from block
        block = hashlib.sha256(block).digest()


def span(stream: Iterator[int], bounds: tuple[int, int]) -> int:
    low, high = bounds
    return low + next(stream) % (high - low + 1)


def alphabets(target: str, k: int) -> list[str]:
    """The alphabet each position of design k of the target draws from, in order (a design's layout)."""
    return list(layout(draws(f"{SEED}|{target}|{k}|layout")))


def layout(stream: Iterator[int]) -> Iterator[str]:
    yield START
    for helix in range(HELICES):
        phase = next(stream) % len(HEPTAD)
        for i in range(span(stream, HELIX_LENGTH)):
            yield HEPTAD[(phase + i) % len(HEPTAD)]
        if helix < HELICES - 1:
            for i in range(span(stream, LOOP_LENGTH)):
                yield LOOP[i % len(LOOP)]


def design(target: str, k: int) -> str:
    """Design k (1 to DESIGNS) of the target: a residue drawn from each position's alphabet."""
    residues = draws(f"{SEED}|{target}|{k}|residues")
    return "".join(alphabet[next(residues) % len(alphabet)] for alphabet in alphabets(target, k))


def candidates(target: str) -> str:
    """The target's six designs as a candidates file: a header line (HEADER) and the sequence on one line each."""
    out = []
    for k in range(1, DESIGNS + 1):
        seq, stream = design(target, k), draws(f"{SEED}|{target}|{k}|header")
        values = {name: base + next(stream) % size for name, (base, size) in METRICS.items()}
        out.append(f"{HEADER.format(target=target, k=k, length=len(seq), **values)}\n{seq}\n")
    return "".join(out)
