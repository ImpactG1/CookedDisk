"""FFmpeg concat merger for combining split session segments into a single MP4."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

from cookeddisk.config import AppConfig
from cookeddisk.drive_client import DriveClient
from cookeddisk.logger import get_logger
from cookeddisk.storage import SegmentRecord, Storage

logger = get_logger("merge")


@dataclass
class MergeProgress:
    phase: str  # "DOWNLOADING", "CONCATENATING", "COMPLETED", "FAILED", "CANCELLED"
    current_segment: int
    total_segments: int
    percent: float
    message: str


def find_ffmpeg_executable(custom_path: Optional[str] = None) -> Optional[str]:
    """Find ffmpeg in custom path, bundled directory, PATH, or common Windows locations."""
    if custom_path and Path(custom_path).is_file():
        return str(Path(custom_path).resolve())

    # Check next to executable / script
    base_dir = Path(__file__).resolve().parent.parent
    bundled_candidates = [
        base_dir / "ffmpeg.exe",
        base_dir / "bin" / "ffmpeg.exe",
        base_dir / "resources" / "ffmpeg.exe",
    ]
    for c in bundled_candidates:
        if c.is_file():
            return str(c)

    # Check system PATH
    which_ffmpeg = shutil.which("ffmpeg")
    if which_ffmpeg:
        return which_ffmpeg

    # Common Windows install locations
    common_paths = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages",
        Path("C:/ffmpeg/bin/ffmpeg.exe"),
        Path("C:/Program Files/ffmpeg/bin/ffmpeg.exe"),
        Path("C:/ProgramData/chocolatey/bin/ffmpeg.exe"),
        Path(os.environ.get("USERPROFILE", "")) / "scoop" / "shims" / "ffmpeg.exe",
    ]
    for p in common_paths:
        if p.is_file():
            return str(p)
        elif p.is_dir():
            # Search winget directory
            found = list(p.glob("**/ffmpeg.exe"))
            if found:
                return str(found[0])

    return None


class MergeSessionWorker:
    """
    Downloads remote segments if missing and runs FFmpeg stream copy concat.
    """

    def __init__(
        self,
        session_id: str,
        output_file_path: Path | str,
        storage: Storage,
        drive_client: Optional[DriveClient] = None,
        config: Optional[AppConfig] = None,
        on_progress: Optional[Callable[[MergeProgress], None]] = None,
    ):
        self.session_id = session_id
        self.output_file_path = Path(output_file_path)
        self.storage = storage
        self.drive_client = drive_client
        self.config = config or AppConfig.load()
        self.on_progress = on_progress

        self._stop_event = threading.Event()
        self._process: Optional[subprocess.Popen] = None
        self._thread: Optional[threading.Thread] = None

    def cancel(self) -> None:
        self._stop_event.set()
        if self._process:
            try:
                self._process.terminate()
            except Exception:
                pass
        logger.info(f"Merge job canceled for session {self.session_id}")

    def is_cancelled(self) -> bool:
        return self._stop_event.is_set()

    def _emit(self, progress: MergeProgress) -> None:
        if self.on_progress:
            try:
                self.on_progress(progress)
            except Exception:
                pass

    def run(self) -> bool:
        ffmpeg_bin = find_ffmpeg_executable(self.config.ffmpeg_custom_path)
        if not ffmpeg_bin:
            err = "FFmpeg executable not found. Please install FFmpeg or set its path in Settings."
            logger.error(err)
            self._emit(MergeProgress("FAILED", 0, 0, 0.0, err))
            return False

        segments = self.storage.get_segments_for_session(self.session_id)
        if not segments:
            err = f"No segments found for session: {self.session_id}"
            logger.error(err)
            self._emit(MergeProgress("FAILED", 0, 0, 0.0, err))
            return False

        temp_dir = self.output_file_path.parent / f"_cookeddisk_merge_{int(time.time())}"
        temp_dir.mkdir(parents=True, exist_ok=True)
        local_segment_paths: List[Path] = []

        try:
            total_segs = len(segments)
            for idx, seg in enumerate(segments, start=1):
                if self.is_cancelled():
                    self._emit(MergeProgress("CANCELLED", idx, total_segs, 0.0, "Merge canceled."))
                    return False

                p = Path(seg.file_path)
                if p.is_file():
                    local_segment_paths.append(p)
                    self._emit(
                        MergeProgress(
                            "DOWNLOADING",
                            idx,
                            total_segs,
                            (idx / total_segs) * 50.0,
                            f"Using local segment {idx}/{total_segs}: {p.name}",
                        )
                    )
                elif seg.drive_file_id and self.drive_client:
                    # Download segment from Google Drive
                    dest = temp_dir / seg.file_name
                    self._emit(
                        MergeProgress(
                            "DOWNLOADING",
                            idx,
                            total_segs,
                            (idx / total_segs) * 50.0,
                            f"Downloading {idx}/{total_segs}: {seg.file_name}...",
                        )
                    )
                    self._download_drive_file(seg.drive_file_id, dest)
                    local_segment_paths.append(dest)
                else:
                    err = f"Missing segment file: {seg.file_name} (not found locally or on Drive)."
                    self._emit(MergeProgress("FAILED", idx, total_segs, 0.0, err))
                    return False

            if self.is_cancelled():
                return False

            # Create FFmpeg concat list
            concat_list_file = temp_dir / "concat_list.txt"
            with open(concat_list_file, "w", encoding="utf-8") as f:
                for seg_path in local_segment_paths:
                    # Escaping single quotes in ffmpeg concat syntax
                    safe_path = str(seg_path.resolve()).replace("'", "'\\''")
                    f.write(f"file '{safe_path}'\n")

            self._emit(
                MergeProgress(
                    "CONCATENATING",
                    total_segs,
                    total_segs,
                    60.0,
                    "Joining segments with FFmpeg stream copy (no re-encoding)...",
                )
            )

            # Ensure output folder exists
            self.output_file_path.parent.mkdir(parents=True, exist_ok=True)

            cmd = [
                ffmpeg_bin,
                "-y",  # overwrite
                "-f", "concat",
                "-safe", "0",
                "-i", str(concat_list_file),
                "-c", "copy",
                "-movflags", "+faststart",
                str(self.output_file_path),
            ]

            logger.info(f"Running FFmpeg: {' '.join(cmd)}")
            creationflags = 0
            if sys.platform == "win32":
                creationflags = subprocess.CREATE_NO_WINDOW

            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                creationflags=creationflags,
            )

            stdout, stderr = self._process.communicate()

            if self.is_cancelled():
                self._emit(MergeProgress("CANCELLED", total_segs, total_segs, 0.0, "Canceled."))
                if self.output_file_path.exists():
                    self.output_file_path.unlink()
                return False

            if self._process.returncode != 0:
                err_msg = f"FFmpeg failed with exit code {self._process.returncode}: {stderr}"
                logger.error(err_msg)
                self._emit(MergeProgress("FAILED", total_segs, total_segs, 0.0, err_msg))
                return False

            self._emit(
                MergeProgress(
                    "COMPLETED",
                    total_segs,
                    total_segs,
                    100.0,
                    f"Successfully merged into {self.output_file_path.name}!",
                )
            )
            logger.info(f"Successfully created merged video: {self.output_file_path}")
            return True

        except Exception as e:
            err = f"Error during merge: {e}"
            logger.error(err, exc_info=True)
            self._emit(MergeProgress("FAILED", 0, 0, 0.0, err))
            return False
        finally:
            # Clean up temp folder
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception:
                pass

    def _download_drive_file(self, file_id: str, dest_path: Path) -> None:
        service = self.drive_client.get_service()
        req = service.files().get_media(fileId=file_id)
        from googleapiclient.http import MediaIoBaseDownload

        with open(dest_path, "wb") as f:
            downloader = MediaIoBaseDownload(f, req, chunksize=8 * 1024 * 1024)
            done = False
            while not done:
                if self.is_cancelled():
                    raise InterruptedError("Download canceled.")
                status, done = downloader.next_chunk()

