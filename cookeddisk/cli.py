"""Command-line interface for headless execution, testing, and automation."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from cookeddisk.config import AppConfig
from cookeddisk.drive_client import DriveAuthManager, DriveClient
from cookeddisk.logger import get_logger, setup_logging
from cookeddisk.obs_client import ObsClient
from cookeddisk.queue_guard import QueueGuard
from cookeddisk.storage import Storage
from cookeddisk.uploader import UploadWorker
from cookeddisk.watcher import FolderWatcher

logger = get_logger("cli")


def run_headless_daemon(config_path: Path | None = None) -> None:
    """Run CookedDisk in background/headless mode without Qt GUI."""
    setup_logging()
    config = AppConfig.load(config_path)
    logger.info("Starting CookedDisk headless daemon...")

    storage = Storage()
    storage.reset_in_progress_segments()

    auth_manager = DriveAuthManager(config)
    drive_client = DriveClient(auth_manager, config)
    obs_client = ObsClient(config)

    queue_guard = QueueGuard(storage=storage, config=config)

    worker = UploadWorker(storage=storage, drive_client=drive_client, config=config)
    watcher = FolderWatcher(
        folder_path=config.obs_recording_dir,
        storage=storage,
        config=config,
        on_segment_ready=lambda s: worker.notify_new_segment(),
    )

    watcher.scan_startup_leftovers()
    worker.start()
    watcher.start()
    obs_client.start_polling()

    logger.info("CookedDisk headless worker running. Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(2.0)
            decision = queue_guard.evaluate()
            if decision.should_pause_recording:
                obs_client.pause_recording()
            elif decision.should_resume_recording:
                obs_client.resume_recording()
            if obs_client.state.current_bitrate_kbps != decision.recommended_bitrate_kbps:
                obs_client.set_recording_bitrate(decision.recommended_bitrate_kbps)
    except KeyboardInterrupt:
        logger.info("Stopping CookedDisk...")
    finally:
        watcher.stop()
        worker.stop()
        obs_client.stop_polling()
        logger.info("Stopped cleanly.")


def run_obs_setup_check() -> None:
    """Run in-terminal OBS diagnostic setup check."""
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    setup_logging()
    config = AppConfig.load()
    obs_client = ObsClient(config)
    results = obs_client.run_setup_check()

    print("\n=== CookedDisk OBS Setup Diagnostic ===")
    for r in results:
        status_symbol = "[PASS]" if r.status == "PASS" else ("[WARN]" if r.status == "WARN" else "[FAIL]")
        clean_fix = r.fix_instruction.replace("\u2192", "->")
        print(f"\n{status_symbol} {r.title}")
        print(f"  Current: {r.current_value}")
        if r.status != "PASS":
            print(f"  Fix:     {clean_fix}")
    print("\n========================================\n")
