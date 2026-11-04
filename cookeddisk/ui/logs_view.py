"""Logs viewer with diagnostic bundle copy and explorer folder reveal."""

from __future__ import annotations

from typing import Optional
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from cookeddisk.logger import (
    get_diagnostics_text,
    get_log_dir,
    open_logs_folder,
)
from cookeddisk.ui.icons import get_icon


class LogsView(QWidget):
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(20, 20, 20, 20)
        self.layout.setSpacing(14)

        # Header
        header = QHBoxLayout()
        header.setSpacing(10)

        title_box = QVBoxLayout()
        title = QLabel("System Logs")
        title.setStyleSheet("font-size: 16px; font-weight: 600;")
        title_box.addWidget(title)

        sub = QLabel("Activity records for file watcher, chunk uploads, and OBS websocket.")
        sub.setStyleSheet("font-size: 12px; color: #888888;")
        title_box.addWidget(sub)
        header.addLayout(title_box)

        header.addStretch()

        self.btn_refresh = QPushButton("Refresh")
        self.btn_refresh.setIcon(get_icon("refresh", "#888888", 16))
        self.btn_refresh.clicked.connect(self.refresh_logs)
        header.addWidget(self.btn_refresh)

        self.btn_copy_diag = QPushButton("Copy diagnostics")
        self.btn_copy_diag.setIcon(get_icon("copy", "#888888", 16))
        self.btn_copy_diag.clicked.connect(self._copy_diagnostics)
        header.addWidget(self.btn_copy_diag)

        self.btn_open_folder = QPushButton("Open logs folder")
        self.btn_open_folder.setIcon(get_icon("folder", "#888888", 16))
        self.btn_open_folder.clicked.connect(open_logs_folder)
        header.addWidget(self.btn_open_folder)

        self.layout.addLayout(header)

        # Log Text Box
        self.log_text = QPlainTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.layout.addWidget(self.log_text)

        self.refresh_logs()

    def refresh_logs(self) -> None:
        log_file = get_log_dir() / "cookeddisk.log"
        if not log_file.is_file():
            self.log_text.setPlainText("Log file has not been created yet.")
            return

        try:
            with open(log_file, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
                # Show last 500 lines
                recent = "".join(lines[-500:])
                self.log_text.setPlainText(recent)
                # Scroll to bottom
                sb = self.log_text.verticalScrollBar()
                sb.setValue(sb.maximum())
        except Exception as e:
            self.log_text.setPlainText(f"Error reading log file: {e}")

    def _copy_diagnostics(self) -> None:
        diag = get_diagnostics_text()
        clipboard = QApplication.clipboard()
        clipboard.setText(diag)
        self.btn_copy_diag.setText("Copied!")
        self.btn_copy_diag.setStyleSheet("color: #4ADE80; font-weight: 600;")
        from PySide6.QtCore import QTimer
        QTimer.singleShot(2500, self._reset_copy_btn)

    def _reset_copy_btn(self) -> None:
        self.btn_copy_diag.setText("Copy diagnostics")
        self.btn_copy_diag.setStyleSheet("")

