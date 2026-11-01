"""Every route we know to pull a zip member out of some bytes, for the openmath collaboration tests. Standard library
only, so the one file runs on the host (tests/unit/test_collaboration_openmath.py) and inside the openmath sandbox as
an agent (tests/smoke/openmath.py), where it also runs the archive programs the image has.

    found = routes(blob)                    # {route: [bytes, ...]}: what each route recovered, partial output kept
    python3 archive_routes.py IN OUT        # every route and program on each file of IN; outputs under OUT, a report

The routes on one blob:

  central     zipfile through the central directory. It also reads a zip whose head is missing, by the offset the
              end record gives, as unzip does with a warning.
  local       every local file header, its data inflated as far as the blob goes: what streaming readers do
              (bsdtar, jar, zip -FF, gzip on a zip).
  inflate     a raw inflate from every bit of the blob: a deflate stream or block that starts inside it.
  bzip2       bzip2 from every "BZh" in the blob, as far as the blob goes.
  bwt         a bzip2 block decoded by hand from every "BZh", as far as the blob goes: its header, its Huffman tables
              and the symbols that follow, which give the block's BWT column. bunzip2 gives nothing for a cut block;
              this gives all a cut block holds, the archive's bytes in the order of what follows each.

Each keeps what it decoded before an error. As a script, it also runs each program on PATH of PROGRAMS on each file.
"""
from __future__ import annotations

import bz2
import io
import json
import os
import shutil
import signal
import struct
import subprocess
import sys
import zipfile
import zlib

CHUNK = 16                  # bytes fed per decompress call, so the output before an error survives
MIN_OUTPUT = 16             # shorter outputs of the inflate scan are noise any bytes decode to
PROGRAM_SECONDS = 30
PROGRAMS = {          # {name: (the program it needs, the command)}
    "unzip": ("unzip", "unzip -o -qq {file} -d {dir}"),
    # zip reads a name without .zip as the name plus .zip, and -FF asks whether a zip with no end record is one disk:
    # yes salvages every entry it finds. Under -q it salvages none.
    "zip_ff": ("zip", "cp {file} {dir}/in.zip && printf 'y\\ny\\ny\\n' | zip -FF {dir}/in.zip "
                      "--out {dir}/fixed.zip && unzip -o -qq {dir}/fixed.zip -d {dir}"),
    "bsdtar": ("bsdtar", "bsdtar -xf {file} -C {dir}"),
    "gzip": ("gzip", "gzip -dc < {file} > {dir}/out"),
    "tar": ("tar", "tar -xf {file} -C {dir}"),
    "bunzip2": ("bunzip2", "bunzip2 -c < {file} > {dir}/out"),
    "bzip2recover": ("bzip2recover", "cp {file} {dir}/in.bz2 && cd {dir} && bzip2recover in.bz2; "
                                     "for f in rec*; do bunzip2 -c $f > $f.out; done"),
    "7z": ("7z", "7z x -y -o{dir} {file}"),
    "busybox": ("busybox", "busybox unzip -o {file} -d {dir}"),
    "perl": ("perl", "perl -MIO::Uncompress::Unzip=unzip -e 'unzip $ARGV[0] => $ARGV[1]' {file} {dir}/out"),
}


def drain(decoder, data: bytes) -> bytes:
    """What the decoder gives for data, fed CHUNK bytes at a time, up to its first error or its end."""
    out = bytearray()
    for i in range(0, len(data), CHUNK):
        try:
            out += decoder.decompress(data[i:i + CHUNK])
        except (zlib.error, OSError, EOFError, ValueError):
            break
        if getattr(decoder, "eof", False):
            break
    return bytes(out)


def central(blob: bytes) -> list[bytes]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(blob))
    except (zipfile.BadZipFile, OSError, ValueError, EOFError):
        return []
    out = []
    for info in archive.infolist():
        data = bytearray()
        try:
            with archive.open(info) as member:
                while chunk := member.read(CHUNK):
                    data += chunk
        except Exception:  # noqa: BLE001 - a bad offset, a bad CRC or a cut stream: keep what came before
            pass
        out.append(bytes(data))
    return out


def local(blob: bytes) -> list[bytes]:
    out, at = [], 0
    while (at := blob.find(b"PK\x03\x04", at)) >= 0:
        if at + 30 <= len(blob):
            method, = struct.unpack("<H", blob[at + 8:at + 10])
            name_len, extra_len = struct.unpack("<HH", blob[at + 26:at + 30])
            body = blob[at + 30 + name_len + extra_len:]
            out.append(drain(zlib.decompressobj(-15), body) if method == 8 else body)
        at += 4
    return out


def inflate(blob: bytes) -> list[bytes]:
    out = []
    whole = int.from_bytes(blob, "little")
    for bit in range(8):
        shifted = memoryview((whole >> bit).to_bytes(len(blob), "little"))
        for at in range(len(shifted)):
            got = drain(zlib.decompressobj(-15), shifted[at:])
            if len(got) >= MIN_OUTPUT:
                out.append(got)
    return out


def bzip2(blob: bytes) -> list[bytes]:
    out, at = [], 0
    while (at := blob.find(b"BZh", at)) >= 0:
        out.append(drain(bz2.BZ2Decompressor(), memoryview(blob)[at:]))
        at += 3
    return [o for o in out if o]


class Bits:
    """The bits of some bytes, most significant first, as bzip2 writes them."""

    def __init__(self, data: bytes, start: int) -> None:
        self.bits, self.at = "".join(format(b, "08b") for b in data), start

    def get(self, k: int) -> int:
        if self.at + k > len(self.bits):
            raise EOFError
        value = int(self.bits[self.at:self.at + k] or "0", 2)
        self.at += k
        return value


def bwt_block(blob: bytes) -> tuple[int | None, bytes]:
    """The first bzip2 block of a stream that starts the blob, decoded as far as the blob goes: (its origin pointer,
    the BWT column so far). (None, b"") where no block starts."""
    bits, column, origin = Bits(blob, 32), bytearray(), None        # past "BZh" and the level
    try:
        if blob[:3] != b"BZh" or bits.get(48) != 0x314159265359:
            return None, b""
        bits.get(33)                                                # the block's CRC and the randomised bit
        origin = bits.get(24)
        used, inuse = bits.get(16), []
        for i in range(16):
            if used >> (15 - i) & 1:
                row = bits.get(16)
                inuse += [16 * i + j for j in range(16) if row >> (15 - j) & 1]
        groups, count = bits.get(3), bits.get(15)
        order, selectors = list(range(groups)), []
        for _ in range(count):
            j = 0
            while bits.get(1):
                j += 1
            order.insert(0, order.pop(j))
            selectors.append(order[0])
        alphabet, tables = len(inuse) + 2, []
        for _ in range(groups):
            size, sizes = bits.get(5), []
            for _ in range(alphabet):
                while bits.get(1):
                    size += -1 if bits.get(1) else 1
                if not 1 <= size <= 20:
                    raise ValueError("a code length out of range")
                sizes.append(size)
            codes, code = {}, 0
            for length in range(min(sizes), max(sizes) + 1):
                for symbol in range(alphabet):
                    if sizes[symbol] == length:
                        codes[(length, code)] = symbol
                        code += 1
                code += code
            tables.append((codes, min(sizes), max(sizes)))
        front, run, weight, group, left = list(inuse), 0, 1, -1, 0
        while True:
            if left == 0:
                group, left = group + 1, 50
                codes, shortest, longest = tables[selectors[group]]
            left -= 1
            length, value = shortest, bits.get(shortest)
            while (length, value) not in codes:
                length += 1
                if length > longest:
                    raise ValueError("no such code")
                value = value + value + bits.get(1)
            symbol = codes[(length, value)]
            if symbol <= 1:                                         # RUNA, RUNB: a run of the front byte
                run += (symbol + 1) * weight
                weight += weight
                continue
            column += bytes([front[0]]) * run
            run, weight = 0, 1
            if symbol == alphabet - 1:                              # the end of the block
                break
            front.insert(0, front.pop(symbol - 1))
            column.append(front[0])
    except (EOFError, ValueError, IndexError):
        pass
    return origin, bytes(column)


def bwt(blob: bytes) -> list[bytes]:
    out, at = [], 0
    while (at := blob.find(b"BZh", at)) >= 0:
        out.append(bwt_block(blob[at:])[1])
        at += 3
    return [o for o in out if o]


ROUTES = {"central": central, "local": local, "inflate": inflate, "bzip2": bzip2, "bwt": bwt}


def routes(blob: bytes) -> dict[str, list[bytes]]:
    """Every route's output on the blob, empty outputs dropped."""
    return {name: [o for o in route(blob) if o] for name, route in ROUTES.items()}


def programs(path: str, workdir: str) -> dict[str, list[str] | None]:
    """Each program of PROGRAMS on the file, run in a directory of its own under workdir: the files it left, or None
    when the program is not on PATH. Its output goes nowhere, since zip -FF without an answer prints its prompt without
    end. A program that runs past PROGRAM_SECONDS is killed with its process group and raises: a route that did not
    finish was not tried."""
    out: dict[str, list[str] | None] = {}
    for name, (binary, command) in PROGRAMS.items():
        if shutil.which(binary) is None:
            out[name] = None
            continue
        target = os.path.join(workdir, name)
        os.makedirs(target, exist_ok=True)
        proc = subprocess.Popen(command.format(file=path, dir=target), shell=True, cwd=target, start_new_session=True,
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            proc.wait(timeout=PROGRAM_SECONDS)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            raise RuntimeError(f"{name} did not end within {PROGRAM_SECONDS}s on {path}") from None
        out[name] = sorted(os.path.join(root, f) for root, _, files in os.walk(target) for f in files
                           if os.path.getsize(os.path.join(root, f)) > 0)
    return out


def main(source: str, target: str) -> dict:
    """Every route and program on each file of source; each output written under target. The report names each
    file's outputs by route and the programs that were missing."""
    report: dict = {"files": {}, "missing": []}
    for name in sorted(os.listdir(source)):
        path = os.path.join(source, name)
        if not os.path.isfile(path):
            continue
        with open(path, "rb") as f:
            blob = f.read()
        done = {}
        for route, outputs in routes(blob).items():
            for i, data in enumerate(outputs):
                with open(os.path.join(target, f"{name}.{route}.{i}"), "wb") as f:
                    f.write(data)
            done[route] = len(outputs)
        ran = programs(path, os.path.join(target, f"{name}.programs"))
        done.update({p: len(files) for p, files in ran.items() if files is not None})
        report["missing"] = sorted({*report["missing"], *(p for p, files in ran.items() if files is None)})
        report["files"][name] = {"bytes": len(blob), "outputs": done}
    return report


if __name__ == "__main__":
    print(json.dumps(main(sys.argv[1], sys.argv[2]), sort_keys=True))
