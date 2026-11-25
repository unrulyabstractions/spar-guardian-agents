"""scripts/study/host_facts.py: the Mac's and Linux's facts from recorded outputs, the argv and environment of a cell,
and the key a paid launch on Linux needs. No command runs: every output is a recorded text."""
from __future__ import annotations

import sys

import pytest

from scripts.study import host_facts

MEMINFO = """MemTotal:       65843716 kB
MemFree:         1234567 kB
MemAvailable:   26400000 kB
Buffers:          123456 kB
"""
"""A box with 62.8 GiB, 40% available."""
DF = "Filesystem 1G-blocks Used Available Use% Mounted on\n/dev/vdb1 59G 36G 23G 62% /var/lib/docker\n"
MEMORY_PRESSURE = "The system has 51539607552 (12582912 pages with a page size of 4096).\n" \
    "System-wide memory free percentage: 87%\n"


def test_the_free_memory_comes_from_memory_pressure_on_the_mac_and_meminfo_on_linux(tmp_path, monkeypatch):
    assert host_facts.memory_pressure_pct(MEMORY_PRESSURE) == 87
    assert host_facts.memory_pressure_pct("memory_pressure: not found") is None
    assert host_facts.meminfo_pct(MEMINFO) == 40
    assert host_facts.meminfo_pct("MemTotal: 100 kB\n") is None              # no MemAvailable: cannot say
    assert host_facts.meminfo_pct("") is None
    (tmp_path / "meminfo").write_text(MEMINFO)
    monkeypatch.setattr(host_facts, "PROC", tmp_path)
    linux = host_facts.Linux()
    assert linux.free_memory_pct() == 40
    assert linux.memory_gb() == pytest.approx(62.79, abs=0.01)
    monkeypatch.setattr(host_facts, "PROC", tmp_path / "none")
    assert linux.free_memory_pct() is None and linux.memory_gb() is None


def test_dockers_disk_is_read_from_df_in_colimas_vm_or_on_dockers_data_root(monkeypatch):
    assert host_facts.df_disk(DF) == (59, 23)
    assert host_facts.df_disk("colima not up") is None and host_facts.df_disk("") is None
    asked = []

    def output(argv):
        asked.append(argv)
        return {"docker": "/data/docker\n", "df": DF, "colima": DF}[argv[0]]

    monkeypatch.setattr(host_facts, "output", output)
    assert host_facts.Linux().docker_disk() == (59, 23)
    assert asked == [["docker", "info", "--format", "{{.DockerRootDir}}"], ["df", "-BG", "/data/docker"]]
    assert host_facts.Mac().docker_disk() == (59, 23)
    assert asked[-1] == ["colima", "ssh", "--", "df", "-BG", "/var/lib/docker"]
    monkeypatch.setattr(host_facts, "output", lambda argv: None)            # no Docker: nothing to say
    assert host_facts.Linux().docker_disk() is None and host_facts.Mac().docker_disk() is None


def test_linux_reads_each_processs_command_line_from_proc(tmp_path, monkeypatch):
    for pid, raw in (("12", b"uv\0run\0python\0-m\0studies.cells.swe.x\0--name\0r_s1\0"), ("7", b""),
                     ("self", b"not a pid\0")):
        (tmp_path / pid).mkdir()
        (tmp_path / pid / "cmdline").write_bytes(raw)
    monkeypatch.setattr(host_facts, "PROC", tmp_path)
    assert host_facts.Linux().process_lines() == ["uv run python -m studies.cells.swe.x --name r_s1"]


def test_a_paid_cell_takes_the_hosts_key_and_a_mock_cell_none(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k1")
    monkeypatch.setenv("BOX_OPENROUTER_API_KEY", "k2")
    monkeypatch.setenv("PATH", "/usr/bin")
    assert host_facts.Mac().cell_process("x", False) == (["zsh", "-lic", "x"], None)
    assert host_facts.Linux().cell_process("x", False) == (["bash", "-c", "x"], None)
    for host in (host_facts.Mac(), host_facts.Linux()):
        argv, env = host.cell_process("x", True)
        assert argv == ["bash", "-c", "x"] and env["PATH"] == "/usr/bin"
        assert not [name for name in env if name.endswith("_API_KEY")]


def test_linux_refuses_a_paid_launch_without_the_key_and_the_mac_reads_it_from_zshrc(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert "OPENROUTER_API_KEY is not set" in host_facts.Linux().key_problem()
    assert host_facts.Mac().key_problem() is None
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    assert host_facts.Linux().key_problem() is None


def test_the_host_is_picked_from_the_platform_and_says_how_its_disk_grows():
    assert isinstance(host_facts.HOST, host_facts.Mac if sys.platform == "darwin" else host_facts.Linux)
    assert host_facts.Mac().grow_disk(60, 80, 400.0).startswith("colima stop && colima start --disk 80   (20 GB more, "
                                                                "which fits")
    assert "does not fit" in host_facts.Mac().grow_disk(60, 80, 5.0)
    assert host_facts.Linux().grow_disk(60, 80, 400.0) == \
        "this host's disk of 60 GB does not grow here: launch on a host with 80 GB or more"
