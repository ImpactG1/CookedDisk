"""Background upload worker running at below-normal priority with verify-and-delete."""

from __future__ import annotations

import ctypes
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from cookeddisk.config import AppConfig
from cookeddisk.drive_client import DriveClient, UploadResult
from cookeddisk.logger import get_logger
from cookeddisk.storage import SegmentRecord, Storage
from cookeddisk.verify import delete_local_file_safely, verify_segment

logger = get_logger("uploader")

# Windows thread priority constants
THREAD_PRIORITY_LOWEST = -2
THREAD_PRIORITY_BELOW_NORMAL = -1
THREAD_PRIORITY_NORMAL = 0


def set_current_thread_below_normal_priority() -> bool:
    """Sets current thread priority to BELOW_NORMAL on Windows to protect game performance."""
    if sys.platform != "win32":
        return False
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetCurrentThread()
        res = kernel32.SetThreadPriority(handle, THREAD_PRIORITY_BELOW_NORMAL)
        if res:
            logger.info("Worker thread priority lowered to THREAD_PRIORITY_BELOW_NORMAL.")
            return True
        else:
            logger.warning("Failed to lower thread priority.")
    except Exception as e:
        logger.warning(f"Could not adjust thread priority: {e}")
    return False


@dataclass
class UploadStatusSnapshot:
    is_active: bool
    current_segment_name: Optional[str]
    current_file_size: int
    current_uploaded_bytes: int
    upload_speed_bytes_sec: float
    total_queue_bytes: int
    total_queue_count: int


class UploadWorker:
    """
    Background worker that picks the oldest READY segment from SQLite,
    uploads it in resumable chunks to Google Drive, verifies MD5 & size,
    and safely deletes the local file only upon 100% verification match.
    """

    def __init__(
        self,
        storage: Storage,
        drive_client: DriveClient,
        config: Optional[AppConfig] = None,
        on_status_change: Optional[Callable[[UploadStatusSnapshot], None]] = None,
        on_segment_verified: Optional[Callable[[SegmentRecord], None]] = None,
    ):
        self.storage = storage
        self.drive_client = drive_client
        self.config = config or AppConfig.load()
        self.on_status_change = on_status_change
        self.on_segment_verified = on_segment_verified

        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._wake_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.RLock()

        # Current upload state tracking
        self.current_segment: Optional[SegmentRecord] = None
        self.current_uploaded_bytes: int = 0
        self.upload_speed_bps: float = 0.0

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._pause_event.clear()
            self._wake_event.clear()
            self._thread = threading.Thread(
                target=self._run_loop,
                name="UploadWorkerThread",
                daemon=True,
            )
            self._thread.start()
            logger.info("Upload worker started.")

    def notify_new_segment(self) -> None:
        """Wakes up worker immediately when a new segment is ready."""
        self._wake_event.set()

    def pause(self) -> None:
        self._pause_event.set()
        logger.info("Upload worker paused.")

    def resume(self) -> None:
        self._pause_event.clear()
        self._wake_event.set()
        logger.info("Upload worker resumed.")

    def is_paused(self) -> bool:
        return self._pause_event.is_set()

    def stop(self) -> None:
        with self._lock:
            self._stop_event.set()
            self._wake_event.set()
            if self._thread and self._thread.is_alive():
                self._thread.join(timeout=5.0)
            self._thread = None
            logger.info("Upload worker stopped.")

    def get_status_snapshot(self) -> UploadStatusSnapshot:
        q_stats = self.storage.get_queue_stats()
        cur_name = self.current_segment.file_name if self.current_segment else None
        cur_size = self.current_segment.file_size if self.current_segment else 0
        return UploadStatusSnapshot(
            is_active=bool(self.current_segment),
            current_segment_name=cur_name,
            current_file_size=cur_size,
            current_uploaded_bytes=self.current_uploaded_bytes,
            upload_speed_bytes_sec=self.upload_speed_bps,
            total_queue_bytes=q_stats["pending_bytes"],
            total_queue_count=q_stats["pending_count"],
        )

    def _emit_status(self) -> None:
        if self.on_status_change:
            try:
                self.on_status_change(self.get_status_snapshot())
            except Exception as e:
                logger.error(f"Error in on_status_change callback: {e}")

    def _run_loop(self) -> None:
        if self.config.thread_priority_below_normal:
            set_current_thread_below_normal_priority()

        while not self._stop_event.is_set():
            if self._pause_event.is_set():
                time.sleep(1.0)
                continue

            segment = self.storage.get_oldest_ready_segment()
            if not segment:
                # No ready segment, wait for notification or timeout
                self.current_segment = None
                self.upload_speed_bps = 0.0
                self._emit_status()
                self._wake_event.wait(timeout=2.0)
                self._wake_event.clear()
                continue

            self._process_segment(segment)

    def _process_segment(self, segment: SegmentRecord) -> None:
        self.current_segment = segment
        local_path = Path(segment.file_path)

        if not local_path.is_file():
            err = f"File disappeared from disk before upload: {segment.file_path}"
            logger.error(err)
            self.storage.mark_segment_failed(segment.id, err)
            self.current_segment = None
            return

        file_size = local_path.stat().st_size
        self.storage.update_segment_uploading(segment.id)
        self._emit_status()

        logger.info(f"Starting upload for segment: {segment.file_name} ({file_size / (1024*1024):.2f} MB)")

        def progress_cb(uploaded: int, total: int, speed: float) -> None:
            self.current_uploaded_bytes = uploaded
            self.upload_speed_bps = speed
            self.storage.update_segment_progress(segment.id, uploaded)
            self._emit_status()

        def is_cancelled() -> bool:
            return self._stop_event.is_set() or self._pause_event.is_set()

        try:
            # Get or create target session folder on Drive
            session_folder_id = self.drive_client.get_or_create_session_folder(segment.session_id)
            self.storage.set_session_drive_folder(segment.session_id, session_folder_id)

            # Upload segment
            result, resumable_uri = self.drive_client.upload_file_resumable(
                file_path=local_path,
                parent_folder_id=session_folder_id,
                resumable_uri=segment.resumable_uri,
                on_progress=progress_cb,
                is_cancel_requested=is_cancelled,
            )

            # Verification step:
            # Compare size and MD5 from Drive with the local file
            logger.info(f"Upload complete for {segment.file_name}. Verifying size & MD5...")
            verified, local_md5, reason = verify_segment(
                local_path=local_path,
                drive_size=result.size,
                drive_md5=result.md5_checksum,
            )

            self.storage.mark_segment_uploaded(
                segment_id=segment.id,
                drive_file_id=result.file_id,
                drive_md5=result.md5_checksum,
                local_md5=local_md5,
            )

            if not verified:
                err_msg = f"Verification failed for {segment.file_name}: {reason}"
                logger.error(err_msg)
                # Do NOT delete local file! Mark as FAILED so it can be re-uploaded or checked.
                self.storage.mark_segment_failed(segment.id, err_msg)
                self.current_segment = None
                self._emit_status()
                return

            # EXACT MATCH CONFIRMED -> Safely delete local file
            logger.info(
                f"Verified {segment.file_name} successfully! (MD5: {local_md5}, Size: {result.size} B). Deleting local copy..."
            )
            deleted, del_msg = delete_local_file_safely(local_path)
            if not deleted:
                logger.warning(f"File verified but local deletion warning: {del_msg}")

            self.storage.mark_segment_verified_and_deleted(segment.id)
            logger.info(f"Local segment {segment.file_name} safely deleted. Disk space reclaimed!")

            if self.on_segment_verified:
                try:
                    self.on_segment_verified(segment)
                except Exception as e:
                    logger.error(f"Error in on_segment_verified: {e}")

        except InterruptedError:
            logger.info(f"Upload of {segment.file_name} was paused or canceled.")
        except Exception as e:
            err = f"Upload error on {segment.file_name}: {e}"
            logger.error(err, exc_info=True)
            self.storage.mark_segment_failed(segment.id, err)
        finally:
            self.current_segment = None
            self.upload_speed_bps = 0.0
            self._emit_status()

