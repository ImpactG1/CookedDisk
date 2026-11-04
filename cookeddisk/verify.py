"""Verification and safe deletion logic for uploaded segments."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Tuple

from cookeddisk.logger import get_logger

logger = get_logger("verify")


def compute_file_md5(file_path: Path | str, chunk_size: int = 65536) -> str:
    """Compute MD5 checksum of a local file in chunks."""
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"File not found: {path}")

    hasher = hashlib.md5()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest().lower()


def verify_segment(
    local_path: Path | str, drive_size: int, drive_md5: str | None
) -> Tuple[bool, str, str]:
    """
    Verify that the local file matches the remote Drive file in size and MD5.

    Returns:
        (success: bool, local_md5: str, message: str)
    """
    path = Path(local_path)
    if not path.is_file():
        return False, "", f"Local file does not exist: {path}"

    local_size = path.stat().st_size
    if local_size != drive_size:
        return (
            False,
            "",
            f"Size mismatch: local={local_size} bytes vs drive={drive_size} bytes",
        )

    local_md5 = compute_file_md5(path)

    if not drive_md5:
        # Drive API v3 usually provides md5Checksum for binary files.
        return False, local_md5, "Drive did not provide an MD5 checksum for verification."

    if local_md5.lower() != drive_md5.strip().lower():
        return (
            False,
            local_md5,
            f"Checksum mismatch: local={local_md5} vs drive={drive_md5}",
        )

    return True, local_md5, "Verification passed: size and MD5 match exactly."


def delete_local_file_safely(local_path: Path | str) -> Tuple[bool, str]:
    """
    Safely delete local file only after successful verification.
    """
    path = Path(local_path)
    if not path.exists():
        return True, "File already deleted."

    try:
        path.unlink(missing_ok=False)
        logger.info(f"Safely deleted local segment: {path.name}")
        return True, "File deleted successfully."
    except Exception as e:
        err_msg = f"Failed to delete local file {path.name}: {e}"
        logger.error(err_msg)
        return False, err_msg

