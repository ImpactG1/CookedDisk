"""Folder watcher monitoring OBS recording folder for completed video segments."""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set

from cookeddisk.config import AppConfig
from cookeddisk.logger import get_logger
from cookeddisk.storage import SegmentRecord, Storage

logger = get_logger("watcher")


@dataclass
class FileObservation:
    path: Path
    last_size: int
    last_change_time: float
    first_seen_time: float
    is_ready: bool = False


class FolderWatcher:
    """
    Monitors OBS's recording folder.
    A segment is 'complete' only when:
    1. OBS has started the next segment (a newer recording file exists in the folder), OR
    2. The file size has been stable for at least `stable_seconds_threshold` seconds.
    Never marks a currently-writing file as ready.
    """

    def __init__(
        self,
        folder_path: Path | str,
        storage: Storage,
        config: Optional[AppConfig] = None,
        on_segment_ready: Optional[Callable[[SegmentRecord], None]] = None,
    ):
        self.folder_path = Path(folder_path) if folder_path else None
        self.storage = storage
        self.config = config or AppConfig.load()
        self.on_segment_ready = on_segment_ready

        self._observations: Dict[str, FileObservation] = {}
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.current_session_id: Optional[str] = None

    def set_folder(self, folder_path: Path | str) -> None:
        with self._lock:
            self.folder_path = Path(folder_path) if folder_path else None
            self._observations.clear()

    def set_session_id(self, session_id: str) -> None:
        with self._lock:
            self.current_session_id = session_id

    def get_session_id_for_file(self, file_path: Path) -> str:
        """Derive or return session id based on current recording session or file timestamp."""
        if self.current_session_id:
            return self.current_session_id
        # Fallback session name from file mtime
        try:
            mtime = file_path.stat().st_mtime
            t_struct = time.localtime(mtime)
            return time.strftime("%Y-%m-%d %H-%M", t_struct)
        except Exception:
            return time.strftime("%Y-%m-%d %H-%M")

    def scan_startup_leftovers(self) -> List[SegmentRecord]:
        """
        On startup: detect leftover segments from a previous run and upload them first.
        Leftover files that already have stable size are marked READY immediately.
        """
        if not self.folder_path or not self.folder_path.is_dir():
            return []

        logger.info(f"Scanning for leftover segments in {self.folder_path}...")
        now = time.time()
        ready_leftovers: List[SegmentRecord] = []

        try:
            files = self._get_candidate_files()
        except Exception as e:
            logger.error(f"Error accessing recording folder during startup scan: {e}")
            return []

        # Find latest file
        latest_file = files[-1] if files else None

        for path in files:
            try:
                stat = path.stat()
                size = stat.st_size
            except OSError:
                continue

            if size <= 0:
                continue

            # Check if this file is actively being written (e.g. modified < 2 seconds ago and is the latest file)
            age = now - stat.st_mtime
            session_id = self.get_session_id_for_file(path)

            record = self.storage.add_or_update_detected_segment(
                session_id=session_id,
                file_path=str(path),
                file_name=path.name,
                file_size=size,
            )

            # If it's already verified/deleted in DB, skip
            if record.status in ("VERIFIED", "DELETED"):
                continue

            # If not latest file, or if age > threshold, it's a finished leftover!
            if path != latest_file or age >= self.config.stable_seconds_threshold:
                self.storage.mark_segment_ready(str(path), size)
                record.status = "READY"
                record.file_size = size
                self._observations[str(path)] = FileObservation(
                    path=path,
                    last_size=size,
                    last_change_time=now - self.config.stable_seconds_threshold - 1,
                    first_seen_time=now - age,
                    is_ready=True,
                )
                ready_leftovers.append(record)
                logger.info(f"Detected ready leftover segment: {path.name} ({size / (1024*1024):.1f} MB)")
            else:
                self._observations[str(path)] = FileObservation(
                    path=path,
                    last_size=size,
                    last_change_time=now,
                    first_seen_time=now,
                    is_ready=False,
                )

        return ready_leftovers

    def _get_candidate_files(self) -> List[Path]:
        if not self.folder_path or not self.folder_path.is_dir():
            return []

        valid_exts = set(ext.lower() for ext in self.config.supported_extensions)
        files = [
            p for p in self.folder_path.iterdir()
            if p.is_file() and p.suffix.lower() in valid_exts
        ]
        # Sort chronologically by mtime, then name
        files.sort(key=lambda p: (p.stat().st_mtime if p.exists() else 0, p.name))
        return files

    def scan_once(self) -> List[SegmentRecord]:
        """
        Scan directory once for segment completion.
        Returns newly completed segments.
        """
        if not self.folder_path or not self.folder_path.is_dir():
            return []

        with self._lock:
            now = time.time()
            try:
                candidate_files = self._get_candidate_files()
            except Exception as e:
                logger.warning(f"Failed to list candidate files: {e}")
                return []

            if not candidate_files:
                return []

            latest_file = candidate_files[-1]
            newly_ready_records: List[SegmentRecord] = []

            for path in candidate_files:
                file_str = str(path)
                try:
                    stat = path.stat()
                    current_size = stat.st_size
                except OSError:
                    continue

                if current_size <= 0:
                    continue

                obs = self._observations.get(file_str)
                if obs is None:
                    # First time seeing this file
                    obs = FileObservation(
                        path=path,
                        last_size=current_size,
                        last_change_time=now,
                        first_seen_time=now,
                        is_ready=False,
                    )
                    self._observations[file_str] = obs

                    session_id = self.get_session_id_for_file(path)
                    self.storage.add_or_update_detected_segment(
                        session_id=session_id,
                        file_path=file_str,
                        file_name=path.name,
                        file_size=current_size,
                    )
                else:
                    if obs.is_ready:
                        continue

                    # Update size if changed
                    if current_size != obs.last_size:
                        obs.last_size = current_size
                        obs.last_change_time = now
                        session_id = self.get_session_id_for_file(path)
                        self.storage.add_or_update_detected_segment(
                            session_id=session_id,
                            file_path=file_str,
                            file_name=path.name,
                            file_size=current_size,
                        )

                # Condition 1: A newer segment exists in the folder
                is_older_than_latest = (path != latest_file)

                # Condition 2: File size has been stable for N seconds
                stable_duration = now - obs.last_change_time
                is_size_stable = (stable_duration >= self.config.stable_seconds_threshold)

                if is_older_than_latest or is_size_stable:
                    obs.is_ready = True
                    self.storage.mark_segment_ready(file_str, current_size)
                    session_id = self.get_session_id_for_file(path)
                    rec = self.storage.add_or_update_detected_segment(
                        session_id=session_id,
                        file_path=file_str,
                        file_name=path.name,
                        file_size=current_size,
                    )
                    newly_ready_records.append(rec)
                    logger.info(
                        f"Segment completed and ready for upload: {path.name} "
                        f"({current_size / (1024*1024):.2f} MB, "
                        f"reason={'next segment started' if is_older_than_latest else f'stable for {stable_duration:.1f}s'})"
                    )
                    if self.on_segment_ready:
                        self.on_segment_ready(rec)

            return newly_ready_records

    def start(self, poll_interval: float = 1.0) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run_loop,
                args=(poll_interval,),
                name="WatcherThread",
                daemon=True,
            )
            self._thread.start()
            logger.info(f"Watcher started on {self.folder_path} (interval {poll_interval}s)")

    def _run_loop(self, poll_interval: float) -> None:
        while not self._stop_event.is_set():
            try:
                self.scan_once()
            except Exception as e:
                logger.error(f"Error in watcher loop: {e}", exc_info=True)
            self._stop_event.wait(poll_interval)

    def stop(self) -> None:
        with self._lock:
            self._stop_event.set()
            if self._thread and self._thread.is_alive():
                self._thread.join(timeout=3.0)
            self._thread = None
            logger.info("Watcher stopped.")
