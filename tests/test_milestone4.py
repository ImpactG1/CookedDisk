"""Unit tests for Milestone 4: PySide6 UI and widgets."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtWidgets import QApplication

from cookeddisk.config import AppConfig
from cookeddisk.obs_client import ObsClient, SetupCheckItem
from cookeddisk.storage import SegmentRecord, Storage
from cookeddisk.ui.dashboard_view import DashboardView
from cookeddisk.ui.icons import get_icon
from cookeddisk.ui.main_window import MainWindow
from cookeddisk.ui.setup_check_view import SetupCheckView
from cookeddisk.ui.theme import get_stylesheet
from cookeddisk.uploader import UploadStatusSnapshot


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_theme_stylesheets(qapp):
    dark_sheet = get_stylesheet("dark")
    assert "background-color" in dark_sheet
    assert "#1E1E1E" in dark_sheet

    light_sheet = get_stylesheet("light")
    assert "background-color" in light_sheet
    assert "#F4F4F5" in light_sheet


def test_svg_icons(qapp):
    icon_dash = get_icon("dashboard", "#2563EB", 24)
    assert not icon_dash.isNull()

    icon_upload = get_icon("upload", "#FFFFFF", 16)
    assert not icon_upload.isNull()


def test_dashboard_view_updates(qapp):
    dash = DashboardView()

    # Test OBS status update
    dash.update_obs_status(is_connected=True, is_recording=True, is_paused=False, timecode="01:23:45")
    assert "Recording" in dash.obs_status_badge.text()
    assert "01:23:45" in dash.obs_status_badge.text()

    # Test Upload status update
    snap = UploadStatusSnapshot(
        is_active=True,
        current_segment_name="segment_001.mp4",
        current_file_size=10485760,
        current_uploaded_bytes=5242880,
        upload_speed_bytes_sec=10485760.0,
        total_queue_bytes=20971520,
        total_queue_count=2,
    )
    dash.update_upload_status(snap)
    assert "segment_001.mp4" in dash.upload_desc_label.text()
    assert dash.progress_bar.value() == 50
    assert "10.0 MB/s" in dash.val_speed.text()
    assert "20 MB" in dash.val_queue.text()

    # Test Segments table update
    seg = SegmentRecord(
        id=1,
        session_id="sess_1",
        file_path="C:/test/seg.mp4",
        file_name="seg.mp4",
        file_size=5000000,
        local_md5="abc",
        drive_file_id="drive123",
        drive_md5="abc",
        status="VERIFIED",
        attempts=1,
        last_error=None,
        resumable_uri=None,
        bytes_uploaded=5000000,
        created_at="2026-11-04T12:00:00Z",
        updated_at="2026-11-04T12:00:30Z",
    )
    dash.update_segments_table([seg])
    assert dash.table.rowCount() == 1
    assert dash.table.item(0, 1).text() == "seg.mp4"
    assert dash.table.item(0, 3).text() == "VERIFIED"


def test_setup_check_view(qapp):
    mock_obs = MagicMock(spec=ObsClient)
    mock_obs.run_setup_check.return_value = [
        SetupCheckItem(
            key="connection",
            title="OBS WebSocket",
            status="PASS",
            current_value="Connected",
            fix_instruction="None",
        ),
        SetupCheckItem(
            key="format",
            title="Recording Format",
            status="FAIL",
            current_value="mp4",
            fix_instruction="Change to Hybrid MP4",
        ),
    ]
    mock_obs.auto_configure_obs.return_value = (True, ["Configured Hybrid MP4", "Enabled Split"])

    view = SetupCheckView(mock_obs)
    view.run_check()
    # Check that cards were populated (2 items + stretch item)
    assert view.cards_layout.count() >= 3

    with patch("PySide6.QtWidgets.QMessageBox.information"):
        view.auto_configure_obs()
        mock_obs.auto_configure_obs.assert_called_once()


def test_main_window_assembly(qapp, tmp_path):
    config = AppConfig(
        obs_recording_dir=str(tmp_path / "obs"),
        theme_mode="dark",
    )
    (tmp_path / "obs").mkdir()

    with patch("cookeddisk.ui.main_window.QSystemTrayIcon.isSystemTrayAvailable", return_value=True):
        win = MainWindow(config)
        assert win.stacked_widget.count() == 5
        win._on_page_changed(1)
        assert win.stacked_widget.currentIndex() == 1
        win._shutdown_services()

