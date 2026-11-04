"""Dialog for executing FFmpeg concat merging on a session."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from cookeddisk.config import AppConfig
from cookeddisk.drive_client import DriveClient
from cookeddisk.merge import MergeProgress, MergeSessionWorker
from cookeddisk.storage import Storage
from cookeddisk.ui.icons import get_icon


class MergeThread(QThread):
    progress = Signal(MergeProgress)
    finished_result = Signal(bool)

    def __init__(
        self,
        session_id: str,
        output_path: Path,
        storage: Storage,
        drive_client: Optional[DriveClient],
        config: AppConfig,
    ):
        super().__init__()
        self.worker = MergeSessionWorker(
            session_id=session_id,
            output_file_path=output_path,
            storage=storage,
            drive_client=drive_client,
            config=config,
            on_progress=self.progress.emit,
        )

    def run(self) -> None:
        res = self.worker.run()
        self.finished_result.emit(res)

    def cancel(self) -> None:
        self.worker.cancel()


class MergeDialog(QDialog):
    def __init__(
        self,
        session_id: str,
        storage: Storage,
        drive_client: Optional[DriveClient],
        config: Optional[AppConfig] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.session_id = session_id
        self.storage = storage
        self.drive_client = drive_client
        self.config = config or AppConfig.load()
        self._thread: Optional[MergeThread] = None

        self.setWindowTitle(f"Merge Session — {session_id}")
        self.setFixedSize(540, 240)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        title = QLabel(f"Export Session: {session_id}")
        title.setStyleSheet("font-size: 14px; font-weight: 600;")
        layout.addWidget(title)

        form = QFormLayout()
        out_row = QHBoxLayout()

        default_out = Path.home() / "Videos" / f"{session_id}_merged.mp4"
        self.txt_out = QLineEdit(str(default_out))
        out_row.addWidget(self.txt_out)

        btn_browse = QPushButton("Browse...")
        btn_browse.clicked.connect(self._browse_output)
        out_row.addWidget(btn_browse)

        form.addRow("Output File:", out_row)
        layout.addLayout(form)

        # Progress Section
        self.lbl_status = QLabel("Ready to merge.")
        self.lbl_status.setStyleSheet("font-size: 12px; color: #888888;")
        layout.addWidget(self.lbl_status)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedHeight(6)
        layout.addWidget(self.progress_bar)

        # Actions Row
        actions = QHBoxLayout()
        actions.addStretch()

        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self._on_cancel_clicked)
        actions.addWidget(self.btn_cancel)

        self.btn_start = QPushButton("Start Merge")
        self.btn_start.setProperty("class", "primaryButton")
        self.btn_start.clicked.connect(self._start_merge)
        actions.addWidget(self.btn_start)

        layout.addLayout(actions)

    def _browse_output(self) -> None:
        chosen, _ = QFileDialog.getSaveFileName(
            self, "Save Merged MP4", self.txt_out.text(), "MP4 Video (*.mp4)"
        )
        if chosen:
            self.txt_out.setText(chosen)

    def _start_merge(self) -> None:
        out_path = Path(self.txt_out.text().strip())
        if not out_path.name.endswith(".mp4"):
            out_path = out_path.with_suffix(".mp4")
            self.txt_out.setText(str(out_path))

        self.btn_start.setEnabled(False)
        self.txt_out.setEnabled(False)

        self._thread = MergeThread(
            session_id=self.session_id,
            output_path=out_path,
            storage=self.storage,
            drive_client=self.drive_client,
            config=self.config,
        )
        self._thread.progress.connect(self._on_progress)
        self._thread.finished_result.connect(self._on_finished)
        self._thread.start()

    def _on_progress(self, p: MergeProgress) -> None:
        self.lbl_status.setText(p.message)
        self.progress_bar.setValue(int(p.percent))

    def _on_finished(self, success: bool) -> None:
        self.btn_start.setEnabled(True)
        self.txt_out.setEnabled(True)
        if success:
            QMessageBox.information(
                self, "Merge Complete", f"Merged MP4 saved successfully to:\n{self.txt_out.text()}"
            )
            self.accept()
        else:
            QMessageBox.warning(
                self, "Merge Incomplete", f"Merge did not complete.\n{self.lbl_status.text()}"
            )

    def _on_cancel_clicked(self) -> None:
        if self._thread and self._thread.isRunning():
            self._thread.cancel()
            self._thread.wait(2000)
        self.reject()

    def closeEvent(self, event) -> None:
        self._on_cancel_clicked()
        super().closeEvent(event)

