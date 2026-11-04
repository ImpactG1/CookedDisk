"""Dashboard view featuring status card, compact metric row, and recent segments table."""

from __future__ import annotations

from typing import List, Optional
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QProgressBar,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from cookeddisk.storage import SegmentRecord
from cookeddisk.uploader import UploadStatusSnapshot


class DashboardView(QWidget):
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(20, 20, 20, 20)
        self.layout.setSpacing(16)

        # 1. Main Status Card
        self.status_card = QFrame()
        self.status_card.setProperty("class", "card")
        card_layout = QVBoxLayout(self.status_card)
        card_layout.setContentsMargins(16, 14, 16, 14)
        card_layout.setSpacing(8)

        # Header with status badge
        header_row = QHBoxLayout()
        header_row.setSpacing(12)

        self.title_label = QLabel("OBS Companion Status")
        self.title_label.setStyleSheet("font-size: 14px; font-weight: 600;")
        header_row.addWidget(self.title_label)

        self.obs_status_badge = QLabel("Idle")
        self.obs_status_badge.setProperty("class", "badgeWarn")
        header_row.addWidget(self.obs_status_badge)

        self.session_title_label = QLabel("")
        self.session_title_label.setStyleSheet("font-size: 12px; color: #888888;")
        header_row.addWidget(self.session_title_label)
        header_row.addStretch()

        card_layout.addLayout(header_row)

        # Active upload status line
        self.upload_desc_label = QLabel("No active upload in progress.")
        self.upload_desc_label.setStyleSheet("font-size: 13px; font-feature-settings: 'tnum' 1;")
        card_layout.addWidget(self.upload_desc_label)

        # Thin progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedHeight(5)
        self.progress_bar.setTextVisible(False)
        card_layout.addWidget(self.progress_bar)

        self.layout.addWidget(self.status_card)

        # 2. Compact Row of Metrics (4 metrics)
        metrics_layout = QHBoxLayout()
        metrics_layout.setSpacing(12)

        # Metric 1: Upload Speed
        self.card_speed, self.val_speed, self.sub_speed = self._create_metric_card(
            "Upload Speed", "0.0 MB/s", "Idle"
        )
        metrics_layout.addWidget(self.card_speed)

        # Metric 2: Queue Size & File Count
        self.card_queue, self.val_queue, self.sub_queue = self._create_metric_card(
            "Pending Queue", "0 MB", "0 files waiting"
        )
        metrics_layout.addWidget(self.card_queue)

        # Metric 3: OBS Video Bitrate
        self.card_bitrate, self.val_bitrate, self.sub_bitrate = self._create_metric_card(
            "Encoder Bitrate", "10,000 kbps", "Normal guard"
        )
        metrics_layout.addWidget(self.card_bitrate)

        # Metric 4: Free Disk Space
        self.card_disk, self.val_disk, self.sub_disk = self._create_metric_card(
            "Free Disk Space", "-- GB", "Drive C: safe"
        )
        metrics_layout.addWidget(self.card_disk)

        self.layout.addLayout(metrics_layout)

        # 3. Table of Recent Segments
        table_title_layout = QHBoxLayout()
        table_label = QLabel("Recent Segments")
        table_label.setStyleSheet("font-size: 14px; font-weight: 600; padding-top: 4px;")
        table_title_layout.addWidget(table_label)
        table_title_layout.addStretch()
        self.layout.addLayout(table_title_layout)

        self.table = QTableWidget()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels([
            "Time",
            "File Name",
            "Size",
            "Status",
            "Drive File ID",
            "Attempts",
        ])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)

        self.layout.addWidget(self.table)

    def _create_metric_card(self, title: str, initial_val: str, initial_sub: str):
        card = QFrame()
        card.setProperty("class", "metricCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(2)

        lbl_title = QLabel(title)
        lbl_title.setProperty("class", "metricTitle")
        layout.addWidget(lbl_title)

        lbl_val = QLabel(initial_val)
        lbl_val.setProperty("class", "metricValue")
        layout.addWidget(lbl_val)

        lbl_sub = QLabel(initial_sub)
        lbl_sub.setProperty("class", "metricSub")
        layout.addWidget(lbl_sub)

        return card, lbl_val, lbl_sub

    def update_obs_status(self, is_connected: bool, is_recording: bool, is_paused: bool, timecode: str):
        if not is_connected:
            self.obs_status_badge.setText("OBS Disconnected")
            self.obs_status_badge.setProperty("class", "badgeFail")
        elif is_recording:
            if is_paused:
                self.obs_status_badge.setText(f"Paused ({timecode})")
                self.obs_status_badge.setProperty("class", "badgeWarn")
            else:
                self.obs_status_badge.setText(f"Recording ({timecode})")
                self.obs_status_badge.setProperty("class", "badgePass")
        else:
            self.obs_status_badge.setText("OBS Ready (Idle)")
            self.obs_status_badge.setProperty("class", "badgeWarn")

        # Force style re-polish
        self.obs_status_badge.style().unpolish(self.obs_status_badge)
        self.obs_status_badge.style().polish(self.obs_status_badge)

    def update_upload_status(self, snapshot: UploadStatusSnapshot):
        if snapshot.is_active and snapshot.current_segment_name:
            curr_mb = snapshot.current_uploaded_bytes / (1024 * 1024)
            total_mb = snapshot.current_file_size / (1024 * 1024)
            pct = int((snapshot.current_uploaded_bytes / snapshot.current_file_size) * 100) if snapshot.current_file_size > 0 else 0
            speed_mb = snapshot.upload_speed_bytes_sec / (1024 * 1024)

            self.upload_desc_label.setText(
                f"Uploading {snapshot.current_segment_name}: {curr_mb:.1f} MB / {total_mb:.1f} MB ({pct}%) — {speed_mb:.1f} MB/s"
            )
            self.progress_bar.setValue(pct)
            self.val_speed.setText(f"{speed_mb:.1f} MB/s")
            self.sub_speed.setText(f"Active chunk transfer")
        else:
            self.upload_desc_label.setText("No active upload in progress.")
            self.progress_bar.setValue(0)
            self.val_speed.setText("0.0 MB/s")
            self.sub_speed.setText("Queue idle")

        # Update queue metric
        queue_mb = snapshot.total_queue_bytes / (1024 * 1024)
        self.val_queue.setText(f"{queue_mb:.0f} MB")
        self.sub_queue.setText(f"{snapshot.total_queue_count} pending files")

    def update_guard_metrics(self, current_bitrate_kbps: int, guard_level: str, free_disk_mb: float):
        self.val_bitrate.setText(f"{current_bitrate_kbps:,} kbps")
        self.sub_bitrate.setText(f"Guard: {guard_level}")

        if free_disk_mb >= 1024:
            self.val_disk.setText(f"{free_disk_mb / 1024:.1f} GB")
        else:
            self.val_disk.setText(f"{free_disk_mb:.0f} MB")

        if free_disk_mb < 300:
            self.sub_disk.setText("Low disk warning")
            self.sub_disk.setStyleSheet("color: #FC8181;")
        else:
            self.sub_disk.setText("Disk headroom safe")
            self.sub_disk.setStyleSheet("color: #707070;")

    def update_segments_table(self, segments: List[SegmentRecord]):
        self.table.setRowCount(len(segments))
        for row, seg in enumerate(segments):
            # Time (last 8 chars or full ISO)
            time_str = seg.created_at[11:19] if len(seg.created_at) >= 19 else seg.created_at
            item_time = QTableWidgetItem(time_str)
            item_time.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            self.table.setItem(row, 0, item_time)

            # File name
            item_name = QTableWidgetItem(seg.file_name)
            self.table.setItem(row, 1, item_name)

            # Size
            size_mb = seg.file_size / (1024 * 1024)
            item_size = QTableWidgetItem(f"{size_mb:.2f} MB")
            item_size.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.table.setItem(row, 2, item_size)

            # Status chip
            status_item = QTableWidgetItem(seg.status)
            if seg.status == "VERIFIED":
                status_item.setForeground(Qt.GlobalColor.darkGreen)
            elif seg.status in ("READY", "UPLOADING"):
                status_item.setForeground(Qt.GlobalColor.darkYellow)
            elif seg.status == "FAILED":
                status_item.setForeground(Qt.GlobalColor.red)
            self.table.setItem(row, 3, status_item)

            # Drive ID
            drive_id_short = (seg.drive_file_id[:12] + "...") if seg.drive_file_id else "—"
            item_drive = QTableWidgetItem(drive_id_short)
            self.table.setItem(row, 4, item_drive)

            # Attempts
            item_att = QTableWidgetItem(str(seg.attempts))
            item_att.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, 5, item_att)

