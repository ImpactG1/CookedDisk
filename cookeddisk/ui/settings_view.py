"""Settings screen for CookedDisk configuration and Google authentication."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from cookeddisk.config import AppConfig
from cookeddisk.drive_client import DriveAuthManager
from cookeddisk.obs_client import ObsClient
from cookeddisk.startup import set_run_at_startup
from cookeddisk.ui.icons import get_icon


class SettingsView(QWidget):
    settings_saved = Signal(AppConfig)
    theme_changed = Signal(str)

    def __init__(
        self,
        config: AppConfig,
        auth_manager: DriveAuthManager,
        obs_client: ObsClient,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.config = config
        self.auth_manager = auth_manager
        self.obs_client = obs_client

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(14)

        # Header
        header = QHBoxLayout()
        title = QLabel("Settings")
        title.setStyleSheet("font-size: 16px; font-weight: 600;")
        header.addWidget(title)
        header.addStretch()

        self.btn_save = QPushButton("Save Settings")
        self.btn_save.setProperty("class", "primaryButton")
        self.btn_save.setIcon(get_icon("check", "#FFFFFF", 16))
        self.btn_save.clicked.connect(self.save_settings)
        header.addWidget(self.btn_save)

        main_layout.addLayout(header)

        # Scroll Area for forms
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        # 1. Google Account Section
        acc_box = QFrame()
        acc_box.setProperty("class", "card")
        acc_layout = QVBoxLayout(acc_box)
        acc_layout.setSpacing(10)

        acc_title = QLabel("Google Drive Account")
        acc_title.setStyleSheet("font-size: 13px; font-weight: 600;")
        acc_layout.addWidget(acc_title)

        acc_sub = QLabel("Files are uploaded to your personal Google Drive (scope: drive.file).")
        acc_sub.setStyleSheet("font-size: 12px; color: #888888;")
        acc_layout.addWidget(acc_sub)

        acc_status_row = QHBoxLayout()
        acc_status_lbl = QLabel("Connected Account:")
        acc_status_lbl.setStyleSheet("font-weight: 500;")
        acc_status_row.addWidget(acc_status_lbl)

        self.lbl_connected_email = QLabel("Checking...")
        acc_status_row.addWidget(self.lbl_connected_email)
        acc_status_row.addStretch()

        self.btn_sign_in = QPushButton("Sign in with Google")
        self.btn_sign_in.clicked.connect(self._do_sign_in)
        acc_status_row.addWidget(self.btn_sign_in)

        self.btn_sign_out = QPushButton("Sign Out")
        self.btn_sign_out.clicked.connect(self._do_sign_out)
        acc_status_row.addWidget(self.btn_sign_out)

        acc_layout.addLayout(acc_status_row)
        layout.addWidget(acc_box)

        # 2. OBS Studio Connection Section
        obs_box = QFrame()
        obs_box.setProperty("class", "card")
        obs_layout = QVBoxLayout(obs_box)
        obs_layout.setSpacing(10)

        obs_title = QLabel("OBS Studio Configuration")
        obs_title.setStyleSheet("font-size: 13px; font-weight: 600;")
        obs_layout.addWidget(obs_title)

        form_obs = QFormLayout()
        form_obs.setSpacing(8)

        # Recording directory
        dir_row = QHBoxLayout()
        self.txt_obs_dir = QLineEdit(self.config.obs_recording_dir)
        self.txt_obs_dir.setPlaceholderText("e.g. C:/Users/YourName/Videos/OBS")
        dir_row.addWidget(self.txt_obs_dir)

        btn_browse_obs = QPushButton("Browse...")
        btn_browse_obs.clicked.connect(self._browse_obs_dir)
        dir_row.addWidget(btn_browse_obs)
        form_obs.addRow("OBS Recording Folder:", dir_row)

        # Host & Port
        hp_row = QHBoxLayout()
        self.txt_obs_host = QLineEdit(self.config.obs_host)
        self.txt_obs_host.setFixedWidth(140)
        hp_row.addWidget(self.txt_obs_host)

        hp_row.addWidget(QLabel("Port:"))
        self.spin_obs_port = QSpinBox()
        self.spin_obs_port.setRange(1, 65535)
        self.spin_obs_port.setValue(self.config.obs_port)
        self.spin_obs_port.setFixedWidth(80)
        hp_row.addWidget(self.spin_obs_port)

        hp_row.addWidget(QLabel("Password:"))
        self.txt_obs_pw = QLineEdit(self.config.obs_password)
        self.txt_obs_pw.setEchoMode(QLineEdit.Password)
        hp_row.addWidget(self.txt_obs_pw)

        btn_test_obs = QPushButton("Test Connection")
        btn_test_obs.clicked.connect(self._test_obs_connection)
        hp_row.addWidget(btn_test_obs)

        form_obs.addRow("WebSocket v5 Server:", hp_row)
        obs_layout.addLayout(form_obs)
        layout.addWidget(obs_box)

        # 3. Queue & Disk Guard Section
        guard_box = QFrame()
        guard_box.setProperty("class", "card")
        guard_layout = QVBoxLayout(guard_box)
        guard_layout.setSpacing(10)

        guard_title = QLabel("Queue & Disk Guard (Prevents disk overflow)")
        guard_title.setStyleSheet("font-size: 13px; font-weight: 600;")
        guard_layout.addWidget(guard_title)

        form_guard = QFormLayout()
        form_guard.setSpacing(8)

        # Thresholds
        t_row = QHBoxLayout()
        self.spin_lower = QSpinBox()
        self.spin_lower.setRange(50, 2000)
        self.spin_lower.setSuffix(" MB")
        self.spin_lower.setValue(int(self.config.queue_threshold_lower_mb))
        t_row.addWidget(QLabel("Lower Bitrate at:"))
        t_row.addWidget(self.spin_lower)

        self.spin_crit = QSpinBox()
        self.spin_crit.setRange(100, 3000)
        self.spin_crit.setSuffix(" MB")
        self.spin_crit.setValue(int(self.config.queue_threshold_critical_mb))
        t_row.addWidget(QLabel("Critical Action at:"))
        t_row.addWidget(self.spin_crit)

        self.spin_hardcap = QSpinBox()
        self.spin_hardcap.setRange(200, 5000)
        self.spin_hardcap.setSuffix(" MB")
        self.spin_hardcap.setValue(int(self.config.queue_threshold_hardcap_mb))
        t_row.addWidget(QLabel("Emergency Cap:"))
        t_row.addWidget(self.spin_hardcap)
        form_guard.addRow("Queue Thresholds:", t_row)

        # Bitrates
        b_row = QHBoxLayout()
        self.spin_target_br = QSpinBox()
        self.spin_target_br.setRange(1000, 50000)
        self.spin_target_br.setSingleStep(500)
        self.spin_target_br.setSuffix(" kbps")
        self.spin_target_br.setValue(self.config.target_bitrate_kbps)
        b_row.addWidget(QLabel("Target Bitrate:"))
        b_row.addWidget(self.spin_target_br)

        self.spin_min_br = QSpinBox()
        self.spin_min_br.setRange(500, 20000)
        self.spin_min_br.setSingleStep(500)
        self.spin_min_br.setSuffix(" kbps")
        self.spin_min_br.setValue(self.config.min_bitrate_kbps)
        b_row.addWidget(QLabel("Minimum Bitrate:"))
        b_row.addWidget(self.spin_min_br)
        form_guard.addRow("Bitrate Limits:", b_row)

        # Critical action combo
        self.combo_crit_action = QComboBox()
        self.combo_crit_action.addItem("Drop to minimum bitrate", "drop_to_min")
        self.combo_crit_action.addItem("Pause recording in OBS", "pause_recording")
        idx = self.combo_crit_action.findData(self.config.critical_action)
        if idx >= 0:
            self.combo_crit_action.setCurrentIndex(idx)
        form_guard.addRow("Critical Queue Action:", self.combo_crit_action)

        # Bandwidth Limiter (Ping Protection)
        self.combo_speed_limit = QComboBox()
        self.combo_speed_limit.addItem("Uncapped (Maximum Upload Speed)", 0)
        self.combo_speed_limit.addItem("20 Mbps — Ultra-Safe Ping Protection", 20000)
        self.combo_speed_limit.addItem("30 Mbps — Competitive Gaming Safe", 30000)
        self.combo_speed_limit.addItem("40 Mbps — Balanced Upload", 40000)
        self.combo_speed_limit.addItem("50 Mbps — High Throughput", 50000)
        self.combo_speed_limit.addItem("60 Mbps — Near-Line Rate", 60000)
        idx_spd = self.combo_speed_limit.findData(self.config.max_upload_kbps)
        if idx_spd >= 0:
            self.combo_speed_limit.setCurrentIndex(idx_spd)
        else:
            self.combo_speed_limit.setCurrentIndex(0)
        form_guard.addRow("Bandwidth Limit (Ping Safe):", self.combo_speed_limit)

        guard_layout.addLayout(form_guard)
        layout.addWidget(guard_box)

        # 4. General Settings
        gen_box = QFrame()
        gen_box.setProperty("class", "card")
        gen_layout = QVBoxLayout(gen_box)
        gen_layout.setSpacing(10)

        gen_title = QLabel("System & UI Settings")
        gen_title.setStyleSheet("font-size: 13px; font-weight: 600;")
        gen_layout.addWidget(gen_title)

        form_gen = QFormLayout()
        form_gen.setSpacing(8)

        # Theme
        self.combo_theme = QComboBox()
        self.combo_theme.addItem("Follow Windows System Setting", "system")
        self.combo_theme.addItem("Dark Theme", "dark")
        self.combo_theme.addItem("Light Theme", "light")
        idx_t = self.combo_theme.findData(self.config.theme_mode)
        if idx_t >= 0:
            self.combo_theme.setCurrentIndex(idx_t)
        self.combo_theme.currentIndexChanged.connect(self._on_theme_changed)
        form_gen.addRow("Appearance Theme:", self.combo_theme)

        # Startup and tray
        self.chk_startup = QCheckBox("Launch CookedDisk at Windows startup")
        self.chk_startup.setChecked(self.config.launch_at_startup)
        form_gen.addRow("", self.chk_startup)

        self.chk_tray = QCheckBox("Minimize to system tray when closing window")
        self.chk_tray.setChecked(self.config.minimize_to_tray_on_close)
        form_gen.addRow("", self.chk_tray)

        # Custom FFmpeg path
        ffmpeg_row = QHBoxLayout()
        self.txt_ffmpeg = QLineEdit(self.config.ffmpeg_custom_path)
        self.txt_ffmpeg.setPlaceholderText("Auto-detected by default")
        ffmpeg_row.addWidget(self.txt_ffmpeg)

        btn_browse_ffmpeg = QPushButton("Browse...")
        btn_browse_ffmpeg.clicked.connect(self._browse_ffmpeg)
        ffmpeg_row.addWidget(btn_browse_ffmpeg)
        form_gen.addRow("FFmpeg Executable Path:", ffmpeg_row)

        gen_layout.addLayout(form_gen)
        layout.addWidget(gen_box)

        layout.addStretch()
        scroll.setWidget(container)
        main_layout.addWidget(scroll)

        self.update_account_display()

    def update_account_display(self) -> None:
        user = self.auth_manager.get_connected_user()
        if user:
            self.lbl_connected_email.setText(user)
            self.lbl_connected_email.setStyleSheet("font-weight: 600; color: #4ADE80;")
            self.btn_sign_in.setEnabled(False)
            self.btn_sign_out.setEnabled(True)
        else:
            self.lbl_connected_email.setText("Not connected")
            self.lbl_connected_email.setStyleSheet("font-weight: 500; color: #F87171;")
            self.btn_sign_in.setEnabled(True)
            self.btn_sign_out.setEnabled(False)

    def _browse_obs_dir(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Select OBS Recording Directory")
        if chosen:
            self.txt_obs_dir.setText(chosen)

    def _browse_ffmpeg(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(
            self, "Select ffmpeg.exe", filter="Executable Files (*.exe)"
        )
        if chosen:
            self.txt_ffmpeg.setText(chosen)

    def _test_obs_connection(self) -> None:
        host = self.txt_obs_host.text().strip()
        port = self.spin_obs_port.value()
        pw = self.txt_obs_pw.text()
        success, message = self.obs_client.connect_with_status(host=host, port=port, password=pw)
        if success:
            QMessageBox.information(
                self, "OBS Connection", f"Connected to OBS WebSocket at {host}:{port} successfully!"
            )
        else:
            QMessageBox.warning(
                self,
                "OBS Connection Failed",
                message,
            )

    def _do_sign_in(self) -> None:
        try:
            self.auth_manager.run_interactive_login()
            self.update_account_display()
            QMessageBox.information(self, "Google Login", "Successfully authenticated with Google Drive!")
        except Exception as e:
            QMessageBox.critical(self, "Authentication Error", f"Google OAuth failed:\n{e}")

    def _do_sign_out(self) -> None:
        self.auth_manager.sign_out()
        self.update_account_display()
        QMessageBox.information(self, "Google Account", "Disconnected from Google Drive.")

    def _on_theme_changed(self) -> None:
        theme = self.combo_theme.currentData()
        self.theme_changed.emit(theme)

    def save_settings(self) -> None:
        self.config.obs_recording_dir = self.txt_obs_dir.text().strip()
        self.config.obs_host = self.txt_obs_host.text().strip()
        self.config.obs_port = self.spin_obs_port.value()
        self.config.obs_password = self.txt_obs_pw.text()

        self.config.queue_threshold_lower_mb = float(self.spin_lower.value())
        self.config.queue_threshold_critical_mb = float(self.spin_crit.value())
        self.config.queue_threshold_hardcap_mb = float(self.spin_hardcap.value())
        self.config.target_bitrate_kbps = self.spin_target_br.value()
        self.config.min_bitrate_kbps = self.spin_min_br.value()
        self.config.critical_action = self.combo_crit_action.currentData()
        self.config.max_upload_kbps = self.combo_speed_limit.currentData()

        self.config.launch_at_startup = self.chk_startup.isChecked()
        self.config.minimize_to_tray_on_close = self.chk_tray.isChecked()
        self.config.theme_mode = self.combo_theme.currentData()
        self.config.ffmpeg_custom_path = self.txt_ffmpeg.text().strip()

        # Update Windows startup registry
        set_run_at_startup(self.config.launch_at_startup)

        self.obs_client.reset_auth_backoff()
        self.config.save()
        self.settings_saved.emit(self.config)
        QMessageBox.information(self, "Settings Saved", "Application settings have been saved.")

