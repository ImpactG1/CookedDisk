"""Sessions view showing all recorded sessions with merge action."""

from __future__ import annotations

from typing import Callable, List, Optional
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from cookeddisk.storage import SessionRecord, Storage
from cookeddisk.ui.icons import get_icon


class SessionsView(QWidget):
    request_merge_session = Signal(str)  # Emits session_id

    def __init__(self, storage: Storage, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.storage = storage

        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(20, 20, 20, 20)
        self.layout.setSpacing(16)

        # Header
        header = QHBoxLayout()
        header.setSpacing(12)

        title = QLabel("Recording Sessions")
        title.setStyleSheet("font-size: 16px; font-weight: 600;")
        header.addWidget(title)
        header.addStretch()

        self.btn_refresh = QPushButton("Refresh")
        self.btn_refresh.setIcon(get_icon("refresh", "#888888", 16))
        self.btn_refresh.clicked.connect(self.refresh)
        header.addWidget(self.btn_refresh)

        self.btn_merge = QPushButton("Merge selected into MP4")
        self.btn_merge.setProperty("class", "primaryButton")
        self.btn_merge.setIcon(get_icon("merge", "#FFFFFF", 16))
        self.btn_merge.clicked.connect(self._on_merge_clicked)
        header.addWidget(self.btn_merge)

        self.layout.addLayout(header)

        # Sessions Table
        self.table = QTableWidget()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels([
            "Session Name",
            "Started (UTC)",
            "Segments",
            "Uploaded",
            "Total Size",
            "Status",
        ])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)

        self.layout.addWidget(self.table)

        self.refresh()

    def refresh(self) -> None:
        sessions = self.storage.list_sessions(limit=100)
        self.table.setRowCount(len(sessions))

        for row, s in enumerate(sessions):
            item_name = QTableWidgetItem(s.name)
            item_name.setData(Qt.UserRole, s.session_id)
            self.table.setItem(row, 0, item_name)

            start_clean = s.started_at[:19].replace("T", " ")
            item_started = QTableWidgetItem(start_clean)
            self.table.setItem(row, 1, item_started)

            item_segs = QTableWidgetItem(f"{s.total_segments}")
            item_segs.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, 2, item_segs)

            item_up = QTableWidgetItem(f"{s.uploaded_segments}")
            item_up.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, 3, item_up)

            size_mb = s.total_bytes / (1024 * 1024)
            item_size = QTableWidgetItem(f"{size_mb:.1f} MB")
            item_size.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.table.setItem(row, 4, item_size)

            item_status = QTableWidgetItem(s.status)
            self.table.setItem(row, 5, item_status)

    def _on_merge_clicked(self) -> None:
        selected_rows = self.table.selectionModel().selectedRows()
        if not selected_rows:
            return
        row = selected_rows[0].row()
        item = self.table.item(row, 0)
        if item:
            session_id = item.data(Qt.UserRole)
            self.request_merge_session.emit(session_id)

