import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "week1_live", Path(__file__).parents[1] / "ops/week1_live.py"
)
live = importlib.util.module_from_spec(spec)
spec.loader.exec_module(live)


def test_elapsed_origins_never_become_live():
    kick = datetime(2026, 9, 11, 0, 35, tzinfo=UTC)
    assert live.origin_status(kick, "T60", kick - timedelta(minutes=30), 10) == "MISSED"
    assert live.origin_status(kick, "T72", kick - timedelta(days=4), 10) == "SCHEDULED"
    assert live.origin_status(kick, "T60", kick - timedelta(minutes=60), 10) == "DUE"
    assert live.origin_status(kick, "T60", kick, 10) == "MISSED"


def test_kickoff_handles_central_conversion_and_australia_designated_game():
    row = {"gameday": "2026-09-10", "gametime": "20:35"}
    assert live.stamp(live.kickoff(row)) == "2026-09-11T00:35:00Z"


def test_immutable_prediction_cannot_be_overwritten(tmp_path):
    path = tmp_path / "record.json"
    live.write_once(path, {"generated_at": "2026-09-11T00:10:00Z"})
    with pytest.raises(ValueError, match="IMMUTABLE"):
        live.write_once(path, {"generated_at": "2026-09-10T00:10:00Z"})


def test_interrupted_write_never_exposes_partial_prediction(tmp_path, monkeypatch):
    path = tmp_path / "record.json"

    def fail_fsync(_fd):
        raise OSError("simulated disk failure")

    with monkeypatch.context() as patch:
        patch.setattr(live.os, "fsync", fail_fsync)
        with pytest.raises(OSError):
            live.write_once(path, {"prediction": 0.6})
    assert not path.exists()
    live.write_once(path, {"prediction": 0.6})
    assert path.exists()


def test_fsync_crossing_kickoff_does_not_publish(tmp_path, monkeypatch):
    deadline = datetime(2026, 9, 11, 0, 35, tzinfo=UTC)
    clock = [deadline - timedelta(seconds=1)]
    monkeypatch.setattr(live, "now", lambda: clock[0])
    actual = live.os.fsync

    def crossing(fd):
        actual(fd)
        clock[0] = deadline

    monkeypatch.setattr(live.os, "fsync", crossing)
    assert (
        live.write_once(tmp_path / "record.json", {"prediction": 0.6}, deadline=deadline) is False
    )
    assert not (tmp_path / "record.json").exists()


def test_raw_bytes_publish_atomically_and_retry_after_interruption(tmp_path, monkeypatch):
    path = tmp_path / "raw.csv"
    with monkeypatch.context() as patch:

        def fail(_fd):
            raise OSError("interrupted raw capture")

        patch.setattr(live.os, "fsync", fail)
        with pytest.raises(OSError):
            live.write_once(path, b"game_id,season\nexample,2026\n")
    assert not path.exists()
    live.write_once(path, b"game_id,season\nexample,2026\n")
    assert path.read_bytes() == b"game_id,season\nexample,2026\n"
