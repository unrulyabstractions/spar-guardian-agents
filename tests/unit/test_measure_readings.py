"""The hand-kept readings (src/measures/measure_readings.py): the file passes its checks, and the rows of the retired
labels (scheming, awareness) are kept as history and marked retired. No model call."""
from __future__ import annotations

import pytest
import yaml

from src.measures import measure_readings


def test_the_rows_of_retired_labels_are_marked_and_the_others_are_not():
    r = measure_readings.readings()
    assert r["retired"] == ["scheming", "awareness"]
    for row in r["rows"]:
        assert row["retired"] is (row["measure"] in ("scheming", "awareness")), row
    assert {row["measure"] for row in r["rows"] if not row["retired"]} == {"cheating", "collusion", "manipulation"}


def test_a_retired_measure_the_file_does_not_name_is_refused(tmp_path):
    data = yaml.safe_load(measure_readings.READINGS_YAML.read_text())
    data["retired"] = ["scheming", "nonesuch"]
    path = tmp_path / "readings.yaml"
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(ValueError, match="retires \\['nonesuch'\\]"):
        measure_readings.readings(path)
