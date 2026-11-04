"""Unit tests for Milestone 1: Watcher, Storage, Resumable Uploader, and Verify-then-Delete."""

import hashlib
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from cookeddisk.config import AppConfig
from cookeddisk.drive_client import DriveClient, UploadResult
from cookeddisk.storage import Storage
from cookeddisk.uploader import UploadWorker
from cookeddisk.verify import compute_file_md5, delete_local_file_safely, verify_segment
from cookeddisk.watcher import FolderWatcher


@pytest.fixture
def temp_env(tmp_path: Path):
    db_file = tmp_path / "test_storage.db"
    storage = Storage(db_path=db_file)
    rec_dir = tmp_path / "obs_recordings"
    rec_dir.mkdir()
    config = AppConfig(
        obs_recording_dir=str(rec_dir),
        stable_seconds_threshold=0.5,
        chunk_size_bytes=256 * 1024,
    )
    return storage, rec_dir, config


def test_storage_session_and_segment_lifecycle(temp_env):
    storage, _, _ = temp_env
    session = storage.get_or_create_session("session_1", "Gaming Session 1")
    assert session.session_id == "session_1"
    assert session.name == "Gaming Session 1"

    # Add segment
    seg = storage.add_or_update_detected_segment(
        session_id="session_1",
        file_path="C:/fake/seg1.mp4",
        file_name="seg1.mp4",
        file_size=1048576,
    )
    assert seg.status == "DETECTED"
    assert seg.file_size == 1048576

    # Mark ready
    storage.mark_segment_ready("C:/fake/seg1.mp4", 1048576)
    oldest = storage.get_oldest_ready_segment()
    assert oldest is not None
    assert oldest.file_name == "seg1.mp4"
    assert oldest.status == "READY"

    # Queue stats
    stats = storage.get_queue_stats()
    assert stats["pending_count"] == 1
    assert stats["pending_bytes"] == 1048576

    # Verify and delete
    storage.mark_segment_verified_and_deleted(oldest.id)
    stats_after = storage.get_queue_stats()
    assert stats_after["pending_count"] == 0
    assert stats_after["pending_bytes"] == 0


def test_watcher_next_segment_and_stability(temp_env):
    storage, rec_dir, config = temp_env
    watcher = FolderWatcher(folder_path=rec_dir, storage=storage, config=config)

    # 1. Create file 1 (simulating active OBS recording)
    f1 = rec_dir / "2026-11-04_21-30-00.mp4"
    f1.write_bytes(b"A" * 1024)

    # First scan: file 1 should be detected, but NOT ready (because it's the latest and not yet stable)
    ready = watcher.scan_once()
    assert len(ready) == 0

    # 2. OBS starts segment 2!
    time.sleep(0.05)
    f2 = rec_dir / "2026-11-04_21-30-30.mp4"
    f2.write_bytes(b"B" * 512)

    # Second scan: file 1 MUST now be marked ready because file 2 has started!
    ready = watcher.scan_once()
    assert len(ready) == 1
    assert ready[0].file_name == "2026-11-04_21-30-00.mp4"
    assert ready[0].status == "READY"

    # File 2 is still actively recording, so it should not be ready yet
    ready2 = watcher.scan_once()
    assert len(ready2) == 0

    # Wait for file 2 size to be stable for >= 0.5s
    time.sleep(0.6)
    ready3 = watcher.scan_once()
    assert len(ready3) == 1
    assert ready3[0].file_name == "2026-11-04_21-30-30.mp4"


def test_verify_then_delete_safe_behavior(tmp_path: Path):
    test_file = tmp_path / "segment_test.mp4"
    content = b"VIDEO_DATA_TEST_12345"
    test_file.write_bytes(content)
    actual_md5 = hashlib.md5(content).hexdigest()
    actual_size = len(content)

    # Exact match -> Should pass
    valid, computed_md5, reason = verify_segment(test_file, actual_size, actual_md5)
    assert valid is True
    assert computed_md5 == actual_md5

    # Size mismatch -> Must fail and must NOT delete
    valid_size_fail, _, _ = verify_segment(test_file, actual_size + 10, actual_md5)
    assert valid_size_fail is False
    assert test_file.exists()

    # MD5 mismatch -> Must fail and must NOT delete
    valid_md5_fail, _, _ = verify_segment(test_file, actual_size, "bad_md5_hash_0000000000000000")
    assert valid_md5_fail is False
    assert test_file.exists()

    # Safe delete after valid verification
    deleted, msg = delete_local_file_safely(test_file)
    assert deleted is True
    assert not test_file.exists()


def test_uploader_worker_end_to_end(temp_env):
    storage, rec_dir, config = temp_env

    # Create dummy segment file
    seg_file = rec_dir / "seg_upload_test.mp4"
    data = b"QUICK_STREAM_CONTENT" * 100
    seg_file.write_bytes(data)
    expected_size = len(data)
    expected_md5 = hashlib.md5(data).hexdigest()

    # Mock DriveClient
    mock_drive = MagicMock(spec=DriveClient)
    mock_drive.get_or_create_session_folder.return_value = "folder_abc_123"
    mock_drive.upload_file_resumable.return_value = (
        UploadResult(
            file_id="drive_file_id_999",
            name=seg_file.name,
            size=expected_size,
            md5_checksum=expected_md5,
        ),
        "https://upload.fake/session/1",
    )

    # Register segment in storage and mark ready
    storage.add_or_update_detected_segment(
        session_id="session_test",
        file_path=str(seg_file),
        file_name=seg_file.name,
        file_size=expected_size,
    )
    storage.mark_segment_ready(str(seg_file), expected_size)

    worker = UploadWorker(storage=storage, drive_client=mock_drive, config=config)

    # Run one pass of worker processing
    ready_seg = storage.get_oldest_ready_segment()
    assert ready_seg is not None
    worker._process_segment(ready_seg)

    # Segment should be verified, marked VERIFIED, and local file deleted!
    assert not seg_file.exists()
    recent = storage.get_recent_segments()
    assert len(recent) == 1
    assert recent[0].status == "VERIFIED"
    assert recent[0].drive_file_id == "drive_file_id_999"
    assert recent[0].drive_md5 == expected_md5

