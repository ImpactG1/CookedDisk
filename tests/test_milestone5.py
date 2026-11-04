"""Unit tests for Milestone 5: FFmpeg merge tool."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cookeddisk.config import AppConfig
from cookeddisk.merge import MergeProgress, MergeSessionWorker, find_ffmpeg_executable
from cookeddisk.storage import Storage


def test_find_ffmpeg_custom_path(tmp_path: Path):
    fake_ffmpeg = tmp_path / "custom_ffmpeg.exe"
    fake_ffmpeg.write_bytes(b"MZFAKEEXE")

    found = find_ffmpeg_executable(str(fake_ffmpeg))
    assert found == str(fake_ffmpeg.resolve())


def test_merge_worker_execution(tmp_path: Path):
    storage = Storage(db_path=tmp_path / "test_merge.db")
    storage.get_or_create_session("sess_1", "Session 1")

    seg1 = tmp_path / "seg1.mp4"
    seg1.write_bytes(b"DATA1")
    seg2 = tmp_path / "seg2.mp4"
    seg2.write_bytes(b"DATA2")

    storage.add_or_update_detected_segment("sess_1", str(seg1), seg1.name, len(b"DATA1"))
    storage.add_or_update_detected_segment("sess_1", str(seg2), seg2.name, len(b"DATA2"))

    out_file = tmp_path / "joined.mp4"
    events = []

    def on_prog(p: MergeProgress):
        events.append(p)

    worker = MergeSessionWorker(
        session_id="sess_1",
        output_file_path=out_file,
        storage=storage,
        on_progress=on_prog,
    )

    # Mock ffmpeg discovery and subprocess Popen
    mock_popen = MagicMock()
    mock_popen.returncode = 0
    mock_popen.communicate.return_value = ("Success", "")

    with patch("cookeddisk.merge.find_ffmpeg_executable", return_value="C:/fake/ffmpeg.exe"):
        with patch("subprocess.Popen", return_value=mock_popen):
            success = worker.run()
            assert success is True
            assert len(events) >= 2
            assert any(e.phase == "COMPLETED" for e in events)


def test_merge_worker_cancel(tmp_path: Path):
    storage = Storage(db_path=tmp_path / "test_merge_cancel.db")
    storage.get_or_create_session("sess_c", "Session C")
    seg1 = tmp_path / "seg_c1.mp4"
    seg1.write_bytes(b"DATA")
    storage.add_or_update_detected_segment("sess_c", str(seg1), seg1.name, 4)

    out_file = tmp_path / "joined_cancel.mp4"
    worker = MergeSessionWorker(
        session_id="sess_c",
        output_file_path=out_file,
        storage=storage,
    )
    worker.cancel()
    assert worker.is_cancelled() is True

