"""Shared fixtures for CookedDisk test suite."""

from pathlib import Path
import pytest

from cookeddisk.config import AppConfig
from cookeddisk.storage import Storage


@pytest.fixture
def temp_env(tmp_path: Path):
    db_file = tmp_path / "test_storage.db"
    storage = Storage(db_path=db_file)
    rec_dir = tmp_path / "obs_recordings"
    rec_dir.mkdir(parents=True, exist_ok=True)
    config = AppConfig(
        obs_recording_dir=str(rec_dir),
        stable_seconds_threshold=0.5,
        chunk_size_bytes=256 * 1024,
    )
    return storage, rec_dir, config

