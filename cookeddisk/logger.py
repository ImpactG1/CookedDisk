"""Structured logging with rotation and diagnostics support for CookedDisk."""

from __future__ import annotations

import logging
import os
import platform
import subprocess
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

from cookeddisk.config import get_default_app_dir

LOGGER_NAME = "CookedDisk"
_logger_initialized = False


def get_log_dir() -> Path:
    log_dir = get_default_app_dir() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    global _logger_initialized
    logger = logging.getLogger(LOGGER_NAME)

    if _logger_initialized:
        return logger

    logger.setLevel(level)
    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)-5.5s] [%(name)s:%(threadName)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(level)
    logger.addHandler(console_handler)

    # Rotating file handler (5 MB max, 5 backups)
    log_file = get_log_dir() / "cookeddisk.log"
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(level)
    logger.addHandler(file_handler)

    _logger_initialized = True
    return logger


def get_logger(module_name: Optional[str] = None) -> logging.Logger:
    if not _logger_initialized:
        setup_logging()
    if module_name:
        return logging.getLogger(f"{LOGGER_NAME}.{module_name}")
    return logging.getLogger(LOGGER_NAME)


def open_logs_folder() -> None:
    """Open the directory containing log files in Windows Explorer."""
    path = get_log_dir()
    if sys.platform == "win32":
        os.startfile(str(path))
    else:
        subprocess.run(["open" if sys.platform == "darwin" else "xdg-open", str(path)])


def get_diagnostics_text() -> str:
    """Generate a clean diagnostics text bundle for support / troubleshooting."""
    from cookeddisk.config import AppConfig

    config = AppConfig.load()
    log_file = get_log_dir() / "cookeddisk.log"
    recent_logs = ""
    if log_file.exists():
        try:
            with open(log_file, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
                recent_logs = "".join(lines[-100:])
        except Exception as e:
            recent_logs = f"Error reading logs: {e}"

    diag = [
        "=== CookedDisk Diagnostics ===",
        f"OS: {platform.platform()} ({platform.architecture()[0]})",
        f"Python: {sys.version}",
        f"App Dir: {get_default_app_dir()}",
        f"Logs Dir: {get_log_dir()}",
        "--- Configuration ---",
        f"OBS Dir: {config.obs_recording_dir}",
        f"OBS WebSocket: {config.obs_host}:{config.obs_port} (auth set: {bool(config.obs_password)})",
        f"Queue Thresholds: Lower={config.queue_threshold_lower_mb}MB, Critical={config.queue_threshold_critical_mb}MB, HardCap={config.queue_threshold_hardcap_mb}MB",
        f"Bitrate Target: {config.target_bitrate_kbps} kbps, Min: {config.min_bitrate_kbps} kbps",
        "--- Recent Logs (last 100 lines) ---",
        recent_logs,
    ]
    return "\n".join(diag)

