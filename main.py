"""Main application entry point for CookedDisk."""

from __future__ import annotations

import argparse
import sys
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from cookeddisk.cli import run_headless_daemon, run_obs_setup_check
from cookeddisk.config import AppConfig
from cookeddisk.logger import setup_logging
from cookeddisk.ui.main_window import MainWindow


def main() -> None:
    parser = argparse.ArgumentParser(description="CookedDisk — Stream OBS gameplay recordings directly to Google Drive.")
    parser.add_argument("--tray", action="store_true", help="Start minimized directly to the system tray.")
    parser.add_argument("--headless", action="store_true", help="Run without graphical interface.")
    parser.add_argument("--check-obs", action="store_true", help="Run OBS Studio diagnostic checks in console.")

    args = parser.parse_args()

    if args.check_obs:
        run_obs_setup_check()
        return

    if args.headless:
        run_headless_daemon()
        return

    setup_logging()

    # Qt Application Setup
    app = QApplication(sys.argv)
    app.setApplicationName("CookedDisk")
    app.setOrganizationName("CookedDisk")

    # High-DPI support
    app.setStyle("Fusion")

    config = AppConfig.load()
    app.setQuitOnLastWindowClosed(not config.minimize_to_tray_on_close)
    window = MainWindow(config)

    if not args.tray:
        window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()

