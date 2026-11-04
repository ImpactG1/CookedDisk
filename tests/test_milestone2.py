"""Unit tests for Milestone 2: Queue guard, Hysteresis, Crash recovery, and Retry logic."""

import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cookeddisk.config import AppConfig
from cookeddisk.drive_client import DriveClient
from cookeddisk.queue_guard import GuardLevel, QueueGuard
from cookeddisk.storage import Storage
from cookeddisk.watcher import FolderWatcher


@pytest.fixture
def test_setup(tmp_path: Path):
    db_file = tmp_path / "test_m2.db"
    storage = Storage(db_path=db_file)
    rec_dir = tmp_path / "recordings"
    rec_dir.mkdir(parents=True, exist_ok=True)
    config = AppConfig(
        obs_recording_dir=str(rec_dir),
        queue_threshold_lower_mb=300.0,
        queue_threshold_critical_mb=600.0,
        queue_threshold_hardcap_mb=850.0,
        hysteresis_recovery_mb=200.0,
        hysteresis_critical_recovery_mb=450.0,
        target_bitrate_kbps=10000,
        min_bitrate_kbps=2500,
    )
    guard = QueueGuard(storage=storage, config=config)
    return storage, guard, rec_dir, config


def test_queue_guard_hysteresis(test_setup):
    storage, guard, _, config = test_setup

    # Mock storage.get_queue_stats and guard.get_free_disk_space_mb
    with patch.object(guard, "get_free_disk_space_mb", return_value=5000.0):
        # 1. Start with 150 MB (Normal)
        with patch.object(storage, "get_queue_stats", return_value={"pending_mb": 150.0}):
            dec = guard.evaluate()
            assert dec.level == GuardLevel.NORMAL
            assert dec.recommended_bitrate_kbps == 10000
            assert not dec.should_pause_recording

        # 2. Queue rises to 350 MB -> Elevated
        with patch.object(storage, "get_queue_stats", return_value={"pending_mb": 350.0}):
            dec = guard.evaluate()
            assert dec.level == GuardLevel.ELEVATED
            assert dec.recommended_bitrate_kbps < 10000
            assert dec.recommended_bitrate_kbps > 2500

        # 3. Queue drops to 250 MB -> HYSTERESIS CHECK: must stay ELEVATED (since 250 > 200 MB recovery)
        with patch.object(storage, "get_queue_stats", return_value={"pending_mb": 250.0}):
            dec = guard.evaluate()
            assert dec.level == GuardLevel.ELEVATED, "Did not hold hysteresis at 250 MB"

        # 4. Queue drops to 180 MB (< 200 MB) -> Restores NORMAL
        with patch.object(storage, "get_queue_stats", return_value={"pending_mb": 180.0}):
            dec = guard.evaluate()
            assert dec.level == GuardLevel.NORMAL
            assert dec.recommended_bitrate_kbps == 10000

        # 5. Queue spikes to 650 MB -> Critical
        with patch.object(storage, "get_queue_stats", return_value={"pending_mb": 650.0}):
            dec = guard.evaluate()
            assert dec.level == GuardLevel.CRITICAL
            assert dec.recommended_bitrate_kbps == 2500

        # 6. Queue drops to 520 MB -> HYSTERESIS CHECK: must stay CRITICAL (since 520 > 450 MB recovery)
        with patch.object(storage, "get_queue_stats", return_value={"pending_mb": 520.0}):
            dec = guard.evaluate()
            assert dec.level == GuardLevel.CRITICAL, "Did not hold critical hysteresis at 520 MB"

        # 7. Queue drops to 400 MB (< 450 MB) -> Drops to ELEVATED
        with patch.object(storage, "get_queue_stats", return_value={"pending_mb": 400.0}):
            dec = guard.evaluate()
            assert dec.level == GuardLevel.ELEVATED

        # 8. Hard Cap: Queue spikes to 900 MB (> 850 MB) -> HARD_CAP with forced pause
        with patch.object(storage, "get_queue_stats", return_value={"pending_mb": 900.0}):
            dec = guard.evaluate()
            assert dec.level == GuardLevel.HARD_CAP
            assert dec.should_pause_recording is True


def test_disk_safety_floor_emergency_pause(test_setup):
    storage, guard, _, _ = test_setup
    # Even if pending queue is tiny (50 MB), if actual disk has < 150 MB free, HARD_CAP must trigger!
    with patch.object(storage, "get_queue_stats", return_value={"pending_mb": 50.0}):
        with patch.object(guard, "get_free_disk_space_mb", return_value=120.0):  # 120 MB free
            dec = guard.evaluate()
            assert dec.level == GuardLevel.HARD_CAP
            assert dec.should_pause_recording is True
            assert "floor" in dec.reason.lower() or "hard cap" in dec.reason.lower()


def test_crash_recovery_resets_interrupted_segments(temp_env):
    storage, _, _ = temp_env
    # Simulate a crash where segments were mid-upload or mid-verification
    seg1 = storage.add_or_update_detected_segment("s1", "C:/video1.mp4", "video1.mp4", 500000)
    seg2 = storage.add_or_update_detected_segment("s1", "C:/video2.mp4", "video2.mp4", 500000)

    storage.update_segment_uploading(seg1.id, resumable_uri="https://fake.url/resume1")
    # Manually simulate VERIFYING status in db
    with storage._get_connection() as conn:
        conn.execute("UPDATE segments SET status = 'VERIFYING' WHERE id = ?", (seg2.id,))
        conn.commit()

    # Before recovery
    recent = storage.get_recent_segments()
    statuses = {s.file_name: s.status for s in recent}
    assert statuses["video1.mp4"] == "UPLOADING"
    assert statuses["video2.mp4"] == "VERIFYING"

    # App restarts -> trigger crash recovery
    recovered_count = storage.reset_in_progress_segments()
    assert recovered_count == 2

    # After recovery: both reset to READY
    recent_after = storage.get_recent_segments()
    for s in recent_after:
        assert s.status == "READY"
        if s.id == seg1.id:
            assert s.resumable_uri == "https://fake.url/resume1"  # preserved!


def test_startup_leftover_segment_recovery(tmp_path: Path):
    db_file = tmp_path / "test_leftovers.db"
    storage = Storage(db_path=db_file)
    rec_dir = tmp_path / "obs_crash_folder"
    rec_dir.mkdir(parents=True, exist_ok=True)
    config = AppConfig(obs_recording_dir=str(rec_dir), stable_seconds_threshold=0.1)

    # Simulate 2 finished segments left behind from previous OBS crash
    leftover1 = rec_dir / "2026-11-04_18-00-00.mp4"
    leftover1.write_bytes(b"OLD_RECORDING_1" * 1000)
    leftover2 = rec_dir / "2026-11-04_18-00-30.mp4"
    leftover2.write_bytes(b"OLD_RECORDING_2" * 1000)

    time.sleep(0.15)
    watcher = FolderWatcher(folder_path=rec_dir, storage=storage, config=config)

    leftovers = watcher.scan_startup_leftovers()
    assert len(leftovers) == 2
    for item in leftovers:
        assert item.status == "READY"

    # Oldest ready segment in storage should be leftover1
    oldest = storage.get_oldest_ready_segment()
    assert oldest is not None
    assert oldest.file_name == leftover1.name
