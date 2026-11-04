"""Windows Run registry helper for launching CookedDisk at startup."""

from __future__ import annotations

import sys
import winreg
from pathlib import Path

REG_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_NAME = "CookedDisk"


def set_run_at_startup(enabled: bool) -> bool:
    """Add or remove CookedDisk from Windows Run registry key."""
    if sys.platform != "win32":
        return False

    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, REG_RUN_KEY, 0, winreg.KEY_SET_VALUE
        )
        if enabled:
            # Point to sys.executable or installed exe
            exe_path = sys.executable
            cmd = f'"{exe_path}" --tray'
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, cmd)
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except FileNotFoundError:
                pass
        winreg.CloseKey(key)
        return True
    except Exception:
        return False


def is_run_at_startup_enabled() -> bool:
    """Check if CookedDisk is configured in Windows Run registry key."""
    if sys.platform != "win32":
        return False

    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, REG_RUN_KEY, 0, winreg.KEY_QUERY_VALUE
        )
        val, _ = winreg.QueryValueEx(key, APP_NAME)
        winreg.CloseKey(key)
        return bool(val)
    except Exception:
        return False

