"""Configuration and settings management for CookedDisk."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict


def get_default_app_dir() -> Path:
    """Return default directory for app state, config and logs."""
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        base = Path(local_app_data)
    else:
        base = Path.home() / ".cookeddisk"
    app_dir = base / "CookedDisk"
    app_dir.mkdir(parents=True, exist_ok=True)
    return app_dir


@dataclass
class AppConfig:
    # OBS recording directory to monitor
    obs_recording_dir: str = ""

    # OBS WebSocket v5 settings
    obs_host: str = "127.0.0.1"
    obs_port: int = 4455
    obs_password: str = ""
    obs_auto_connect: bool = True

    # Google Drive configuration
    drive_base_folder_name: str = "CookedDisk"
    # Chunk size for resumable uploads (must be multiple of 256 KiB; default 8 MB)
    chunk_size_bytes: int = 8 * 1024 * 1024

    # Segment detection settings
    segment_length_sec: int = 30
    stable_seconds_threshold: float = 3.0  # seconds with zero file size change
    supported_extensions: list[str] = field(
        default_factory=lambda: [".mp4", ".mkv", ".mov", ".ts"]
    )

    # Queue guard thresholds (in Megabytes)
    queue_threshold_lower_mb: float = 300.0     # Step down bitrate
    queue_threshold_critical_mb: float = 600.0  # Drop to minimum or pause
    queue_threshold_hardcap_mb: float = 850.0   # Emergency stop / disk guard
    hysteresis_recovery_mb: float = 200.0       # Queue must drop below this to restore full bitrate
    hysteresis_critical_recovery_mb: float = 450.0 # Queue must drop below this to leave critical state

    # Bitrate limits (kbps)
    target_bitrate_kbps: int = 10000
    min_bitrate_kbps: int = 2500
    critical_action: str = "drop_to_min"  # "drop_to_min" or "pause_recording"

    # Worker options
    thread_priority_below_normal: bool = True
    max_upload_retries: int = 10
    retry_base_delay_sec: float = 2.0
    retry_max_delay_sec: float = 60.0
    max_upload_kbps: int = 0  # 0 = Uncapped; otherwise cap speed (e.g. 40000 = 40 Mbps ping protection)

    # System and UI settings
    launch_at_startup: bool = False
    minimize_to_tray_on_close: bool = False
    theme_mode: str = "system"  # "system", "dark", "light"
    ffmpeg_custom_path: str = ""

    @classmethod
    def load(cls, config_path: Path | None = None) -> AppConfig:
        """Load configuration from JSON file or return defaults."""
        path = config_path or (get_default_app_dir() / "config.json")
        if not path.is_file():
            config = cls()
            config.save(path)
            return config

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Filter unknown keys to prevent crashes across versions
            valid_keys = cls.__dataclass_fields__.keys()
            filtered = {k: v for k, v in data.items() if k in valid_keys}
            return cls(**filtered)
        except Exception:
            return cls()

    def save(self, config_path: Path | None = None) -> None:
        """Save configuration to JSON file."""
        path = config_path or (get_default_app_dir() / "config.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, indent=2)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

