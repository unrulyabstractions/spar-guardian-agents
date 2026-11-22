"""The eval log's events streamed (src/measures/eval_log_events.py) read what json.loads reads, and the viewer's index
measures in as many processes as the machine holds (transcript_index.workers_for)."""
from __future__ import annotations

import json
import math
import zipfile
from datetime import datetime

import pytest

from src.measures import eval_log_events as ev
from src.measures.run_calls import eval_times
from src.ui import transcript_index as ti


def tool(i: int, start: str, end: str | None, span: str = "s1") -> dict:
    return {"event": "tool", "id": f"call_{i}", "span_id": span, "function": "bash", "timestamp": start,
            "completed": end, "arguments": {"cmd": f"echo {i}", "n": 12345678901234567890123}}


def sample(sid, epoch: int, events: list[dict]) -> dict:
    """A sample as Inspect writes it: the events, then attachments, with numbers yajl refuses on both sides."""
    return {"id": sid, "epoch": epoch, "store": {"sk": 18446576086267758912, "nan": math.nan, "inf": -math.inf},
            "events": events, "timelines": [], "attachments": {"a" * 8: "x" * 50_000 + "1234567890123456789012"}}


def write_log(path, samples: list[dict]) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("header.json", "{}")
        for s in samples:
            z.writestr(f"samples/{s['id']}_epoch_{s['epoch']}.json", json.dumps(s))
        z.writestr("summaries.json", json.dumps([{"id": s["id"], "epoch": s["epoch"]} for s in samples]))


def expected_times(samples: list[dict]) -> dict:
    """What eval_times read before the stream: each member json.loads'd whole."""
    out = {}
    for s in samples:
        for e in s["events"]:
            if e.get("event") == "tool" and e.get("timestamp"):
                end = e.get("completed")
                out[e["id"]] = (datetime.fromisoformat(e["timestamp"]).timestamp(),
                                datetime.fromisoformat(end).timestamp() if end else None)
    return out


@pytest.mark.parametrize("chunk", [7, 64, 1 << 22])
def test_the_stream_reads_the_tool_times_json_loads_reads_whatever_the_chunk(tmp_path, monkeypatch, chunk):
    monkeypatch.setattr(ev, "CHUNK", chunk)
    ev._read.cache_clear()
    events = [{"event": "span_begin", "id": "s1", "parent_id": None, "type": "agent", "name": "k7q2m",
               "timestamp": "2026-11-22T13:40:11.123456+00:00"},
              tool(1, "2026-11-22T13:40:12.000001+00:00", "2026-11-22T13:40:13.5+00:00"),
              {"event": "model", "input": [1, 2, 99999999999999999999], "timestamp": "2026-11-22T13:40:14+00:00"},
              tool(2, "2026-11-22T13:40:15+00:00", None), tool(3, "", None)]
    samples = [sample("poem", 1, events)]
    log = tmp_path / "x.eval"
    write_log(log, samples)
    assert eval_times(log) == expected_times(samples)
    [read] = ev.log_samples(log)
    assert [e.event for e in read.events] == ["span_begin", "tool", "model", "tool", "tool"]
    assert read.events[0].type == "agent" and read.events[0].name == "k7q2m" and read.events[1].span_id == "s1"
    assert read.events[3].completed is None


def test_samples_come_in_read_eval_logs_order_an_int_id_zero_filled(tmp_path):
    ev._read.cache_clear()
    samples = [sample(10, 1, [tool(10, "2026-11-22T13:40:12+00:00", None)]),
               sample(9, 1, [tool(9, "2026-11-22T13:40:12+00:00", None)]),
               sample(1, 2, [tool(1, "2026-11-22T13:40:12+00:00", None)])]
    log = tmp_path / "x.eval"
    write_log(log, samples)
    assert [s.events[0].id for s in ev.log_samples(log)] == ["call_9", "call_10", "call_1"]


def test_a_member_yajl_cannot_read_is_a_value_error(tmp_path):
    ev._read.cache_clear()
    log = tmp_path / "x.eval"
    with zipfile.ZipFile(log, "w") as z:
        z.writestr("samples/a_epoch_1.json", '{"events": [{"event": "tool", ')
    with pytest.raises(ValueError):
        ev.log_samples(log)


@pytest.mark.parametrize(("gb", "cores", "workers"), [
    (64, 16, 15),     # the cores bound a big machine
    (24, 16, 15),
    (8, 16, 6),       # memory bounds it: half of 8 GB at 600 MB each
    (3, 8, 2),        # an 8 GB laptop with 3 GB available
    (0.5, 8, 1),      # never fewer than one
    (64, 1, 1),       # one core
    (64, None, 1),    # os.cpu_count unknown
])
def test_viewer_index_workers_follow_cores_and_available_memory(gb, cores, workers):
    assert ti.workers_for(int(gb * 2**30), cores) == workers
