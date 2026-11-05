"""Main application window uniting navigation, views, background services, and system tray."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QAction, QCloseEvent, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QMainWindow,
    QMenu,
    QStackedWidget,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from cookeddisk.config import AppConfig
from cookeddisk.drive_client import DriveAuthManager, DriveClient
from cookeddisk.logger import get_logger
from cookeddisk.obs_client import ObsClient, ObsState
from cookeddisk.queue_guard import GuardDecision, QueueGuard
from cookeddisk.storage import Storage
from cookeddisk.ui.dashboard_view import DashboardView
from cookeddisk.ui.icons import get_icon
from cookeddisk.ui.logs_view import LogsView
from cookeddisk.ui.merge_dialog import MergeDialog
from cookeddisk.ui.nav_bar import NavBar
from cookeddisk.ui.sessions_view import SessionsView
from cookeddisk.ui.settings_view import SettingsView
from cookeddisk.ui.setup_check_view import SetupCheckView
from cookeddisk.ui.theme import get_stylesheet
from cookeddisk.uploader import UploadWorker
from cookeddisk.watcher import FolderWatcher

logger = get_logger("main_window")


class MainWindow(QMainWindow):
    def __init__(self, config: Optional[AppConfig] = None):
        super().__init__()
        self.config = config or AppConfig.load()
        self.setWindowTitle("CookedDisk — Companion for OBS Studio")
        self.resize(1020, 680)
        self.setMinimumSize(880, 560)

        # 1. Initialize core domain services
        self.storage = Storage()
        self.auth_manager = DriveAuthManager(self.config)
        self.drive_client = DriveClient(self.auth_manager, self.config)
        self.obs_client = ObsClient(self.config)

        # 2. Crash recovery on startup
        self.storage.reset_in_progress_segments()

        # 3. Queue Guard
        self.queue_guard = QueueGuard(
            storage=self.storage,
            config=self.config,
            on_guard_action=self._handle_guard_action,
        )

        # 4. Upload worker
        self.upload_worker = UploadWorker(
            storage=self.storage,
            drive_client=self.drive_client,
            config=self.config,
        )

        # 5. Folder Watcher
        self.watcher = FolderWatcher(
            folder_path=self.config.obs_recording_dir,
            storage=self.storage,
            config=self.config,
            on_segment_ready=lambda seg: self.upload_worker.notify_new_segment(),
        )

        # Scan for startup leftovers
        self.watcher.scan_startup_leftovers()

        # 6. Build UI Layout
        central_widget = QWidget(self)
        self.setCentralWidget(central_widget)

        main_hlayout = QHBoxLayout(central_widget)
        main_hlayout.setContentsMargins(0, 0, 0, 0)
        main_hlayout.setSpacing(0)

        # Left Navigation Sidebar
        self.nav_bar = NavBar(self)
        self.nav_bar.page_changed.connect(self._on_page_changed)
        main_hlayout.addWidget(self.nav_bar)

        # Content Stacked Widget
        self.stacked_widget = QStackedWidget(self)
        self.stacked_widget.setObjectName("contentContainer")

        # Page 0: Dashboard
        self.view_dashboard = DashboardView(self)
        self.stacked_widget.addWidget(self.view_dashboard)

        # Page 1: Sessions
        self.view_sessions = SessionsView(self.storage, self)
        self.view_sessions.request_merge_session.connect(self._open_merge_dialog)
        self.stacked_widget.addWidget(self.view_sessions)

        # Page 2: Setup Check
        self.view_setup = SetupCheckView(self.obs_client, self)
        self.stacked_widget.addWidget(self.view_setup)

        # Page 3: Settings
        self.view_settings = SettingsView(
            self.config, self.auth_manager, self.obs_client, self
        )
        self.view_settings.settings_saved.connect(self._on_settings_saved)
        self.view_settings.theme_changed.connect(self._apply_theme)
        self.stacked_widget.addWidget(self.view_settings)

        # Page 4: Logs
        self.view_logs = LogsView(self)
        self.stacked_widget.addWidget(self.view_logs)

        main_hlayout.addWidget(self.stacked_widget)

        # 7. Apply theme stylesheet
        self._apply_theme(self.config.theme_mode)

        # 8. Setup System Tray Icon
        self._init_system_tray()

        # 9. Start background threads
        self.upload_worker.start()
        if self.config.obs_recording_dir:
            self.watcher.start(poll_interval=1.0)
        self.obs_client.start_polling(interval_sec=2.0)

        # 10. Periodic UI update timer (1 Hz)
        self.ui_timer = QTimer(self)
        self.ui_timer.timeout.connect(self._on_ui_tick)
        self.ui_timer.start(1000)

        # Set user display in nav
        self.nav_bar.set_connected_user(self.auth_manager.get_connected_user())

    def _init_system_tray(self) -> None:
        self.tray_icon = QSystemTrayIcon(self)
        
        # Load app icon from resources
        icon = None
        base_dir = Path(__file__).resolve().parent.parent.parent
        for icon_path in [
            base_dir / "resources" / "app_icon.ico",
            base_dir / "resources" / "logo.png",
            base_dir / "logo.png",
        ]:
            if icon_path.is_file():
                icon = QIcon(str(icon_path))
                break
        
        if not icon or icon.isNull():
            icon = get_icon("upload", "#2563EB", 32)

        self.setWindowIcon(icon)
        self.tray_icon.setIcon(icon)

        tray_menu = QMenu()
        self.tray_status_action = QAction("CookedDisk: Idle", self)
        self.tray_status_action.setEnabled(False)
        tray_menu.addAction(self.tray_status_action)
        tray_menu.addSeparator()

        act_show = QAction("Open Dashboard", self)
        act_show.triggered.connect(self._show_window)
        tray_menu.addAction(act_show)

        self.act_pause_resume = QAction("Pause Uploads", self)
        self.act_pause_resume.triggered.connect(self._toggle_upload_pause)
        tray_menu.addAction(self.act_pause_resume)

        tray_menu.addSeparator()
        act_quit = QAction("Quit CookedDisk", self)
        act_quit.triggered.connect(self._quit_app)
        tray_menu.addAction(act_quit)

        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self._on_tray_activated)
        self.tray_icon.show()

    def _show_window(self) -> None:
        self.show()
        self.setWindowState(self.windowState() & ~Qt.WindowMinimized | Qt.WindowActive)
        self.activateWindow()

    def _toggle_upload_pause(self) -> None:
        if self.upload_worker.is_paused():
            self.upload_worker.resume()
            self.act_pause_resume.setText("Pause Uploads")
        else:
            self.upload_worker.pause()
            self.act_pause_resume.setText("Resume Uploads")

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            if self.isVisible():
                self.hide()
            else:
                self._show_window()

    def _on_page_changed(self, idx: int) -> None:
        self.stacked_widget.setCurrentIndex(idx)
        if idx == 1:
            self.view_sessions.refresh()
        elif idx == 2:
            self.view_setup.run_check()
        elif idx == 4:
            self.view_logs.refresh_logs()

    def _open_merge_dialog(self, session_id: str) -> None:
        dlg = MergeDialog(
            session_id=session_id,
            storage=self.storage,
            drive_client=self.drive_client,
            config=self.config,
            parent=self,
        )
        dlg.exec()

    def _apply_theme(self, mode: str) -> None:
        sheet = get_stylesheet(mode)  # type: ignore
        qApp = QApplication.instance()
        if qApp:
            qApp.setStyleSheet(sheet)

    def _on_settings_saved(self, updated_cfg: AppConfig) -> None:
        self.config = updated_cfg
        self.watcher.set_folder(self.config.obs_recording_dir)
        self.watcher.config = self.config
        self.upload_worker.config = self.config
        self.queue_guard.config = self.config
        self.obs_client.config = self.config
        self.nav_bar.set_connected_user(self.auth_manager.get_connected_user())
        logger.info("Applied updated settings across all sub-services.")

    def _handle_guard_action(self, decision: GuardDecision) -> None:
        """Applies QueueGuard recommendations to OBS."""
        if decision.should_pause_recording:
            if not self.obs_client.state.is_paused and self.obs_client.state.is_recording:
                self.obs_client.pause_recording()
                logger.warning(f"Disk Guard paused recording: {decision.reason}")
        elif decision.should_resume_recording:
            if self.obs_client.state.is_paused:
                self.obs_client.resume_recording()
                logger.info("Disk Guard resumed recording as queue drained.")

        if self.obs_client.state.current_bitrate_kbps != decision.recommended_bitrate_kbps:
            self.obs_client.set_recording_bitrate(decision.recommended_bitrate_kbps)

    def _on_ui_tick(self) -> None:
        # 1. Evaluate Guard
        decision = self.queue_guard.evaluate()

        # 2. Update dashboard OBS state
        obs_st = self.obs_client.state
        self.view_dashboard.update_obs_status(
            is_connected=obs_st.is_connected,
            is_recording=obs_st.is_recording,
            is_paused=obs_st.is_paused,
            timecode=obs_st.record_timecode,
        )

        # 3. Update upload status & metrics
        up_snap = self.upload_worker.get_status_snapshot()
        self.view_dashboard.update_upload_status(up_snap, self.config.max_upload_kbps)
        self.view_dashboard.update_guard_metrics(
            current_bitrate_kbps=self.queue_guard.current_bitrate_kbps,
            guard_level=decision.level.value,
            free_disk_mb=decision.free_disk_mb,
        )

        # 4. Update recent segments table
        recent_segs = self.storage.get_recent_segments(limit=30)
        self.view_dashboard.update_segments_table(recent_segs)

        # 5. Tray icon tooltip & status
        status_txt = "Idle"
        if obs_st.is_recording:
            status_txt = f"Recording ({obs_st.record_timecode})"
        speed_mb = up_snap.upload_speed_bytes_sec / (1024 * 1024)
        tray_tip = f"CookedDisk: {status_txt} | Upload: {speed_mb:.1f} MB/s | Queue: {up_snap.total_queue_bytes / (1024*1024):.0f} MB"
        self.tray_icon.setToolTip(tray_tip)
        self.tray_status_action.setText(tray_tip)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.config.minimize_to_tray_on_close and self.tray_icon.isVisible():
            self.hide()
            self.tray_icon.showMessage(
                "CookedDisk",
                "CookedDisk is still running in the background.",
                QSystemTrayIcon.Information,
                2000,
            )
            event.ignore()
        else:
            self._shutdown_services()
            event.accept()

    def _shutdown_services(self) -> None:
        logger.info("Shutting down CookedDisk services...")
        self.ui_timer.stop()
        self.watcher.stop()
        self.upload_worker.stop()
        self.obs_client.stop_polling()
        self.obs_client.disconnect()
        logger.info("All services terminated cleanly.")

    def _quit_app(self) -> None:
        self.config.minimize_to_tray_on_close = False
        self._shutdown_services()
        self.tray_icon.hide()
        QApplication.quit()

