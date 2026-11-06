"""Unit tests for Milestone 3: OBS WebSocket client, Setup check, and Adaptive bitrate control."""

from unittest.mock import MagicMock, patch

import pytest

from cookeddisk.config import AppConfig
from cookeddisk.obs_client import ObsClient, SetupCheckItem
from cookeddisk.queue_guard import GuardLevel, QueueGuard
from cookeddisk.storage import Storage


@pytest.fixture
def obs_mock_setup(tmp_path):
    config = AppConfig(
        obs_host="localhost",
        obs_port=4455,
        obs_password="password123",
        obs_recording_dir=str(tmp_path / "obs_out"),
        target_bitrate_kbps=10000,
        min_bitrate_kbps=2500,
    )
    client = ObsClient(config=config)
    return client, config


def test_obs_connection_and_state(obs_mock_setup):
    client, config = obs_mock_setup

    mock_req_client = MagicMock()
    # Mock record status response
    rec_status = MagicMock()
    rec_status.output_active = True
    rec_status.output_paused = False
    rec_status.output_timecode = "00:15:30"
    rec_status.output_bytes = 104857600
    mock_req_client.get_record_status.return_value = rec_status

    rec_dir = MagicMock()
    rec_dir.record_directory = config.obs_recording_dir
    mock_req_client.get_record_directory.return_value = rec_dir

    with patch("obsws_python.ReqClient", return_value=mock_req_client):
        success = client.connect()
        assert success is True
        assert client.is_connected() is True
        assert client.state.is_recording is True
        assert client.state.is_paused is False
        assert client.state.record_timecode == "00:15:30"
        assert client.state.recording_directory == config.obs_recording_dir


def test_obs_pause_and_resume(obs_mock_setup):
    client, _ = obs_mock_setup
    mock_req_client = MagicMock()
    client._client = mock_req_client
    client.state.is_connected = True

    client.pause_recording()
    mock_req_client.pause_record.assert_called_once()

    client.resume_recording()
    mock_req_client.resume_record.assert_called_once()


def test_obs_set_bitrate(obs_mock_setup):
    client, _ = obs_mock_setup
    mock_req_client = MagicMock()
    client._client = mock_req_client
    client.state.is_connected = True

    applied = client.set_recording_bitrate(6000)
    assert applied is True
    assert client.state.current_bitrate_kbps == 6000
    mock_req_client.set_output_settings.assert_called()


def test_setup_check_disconnected(obs_mock_setup):
    client, _ = obs_mock_setup
    client.disconnect()

    with patch.object(client, "connect", return_value=False):
        results = client.run_setup_check()
        assert len(results) >= 5
        conn_check = next(r for r in results if r.key == "connection")
        assert conn_check.status == "FAIL"
        assert "WebSocket Server Settings" in conn_check.fix_instruction


def test_setup_check_connected_with_failures(obs_mock_setup):
    client, config = obs_mock_setup
    mock_req_client = MagicMock()
    client._client = mock_req_client
    client.state.is_connected = True

    # Mock responses: wrong path, software x264 encoder, standard MP4 format
    rec_dir = MagicMock()
    rec_dir.record_directory = "C:/different/folder"
    mock_req_client.get_record_directory.return_value = rec_dir

    def mock_get_profile(category, parameter):
        mock_p = MagicMock()
        if parameter == "RecFormat":
            mock_p.parameter_value = "mp4"  # standard unfragmented mp4 -> FAIL
        elif parameter == "RecEncoder":
            mock_p.parameter_value = "obs_x264"  # software encoder -> FAIL
        elif parameter == "RecBitrate":
            mock_p.parameter_value = "10000"  # good bitrate -> PASS
        elif parameter == "RecSplitFile":
            mock_p.parameter_value = "false"  # not splitting -> FAIL
        else:
            mock_p.parameter_value = ""
        return mock_p

    mock_req_client.get_profile_parameter.side_effect = mock_get_profile

    results = client.run_setup_check()
    res_dict = {r.key: r for r in results}

    assert res_dict["connection"].status == "PASS"
    assert res_dict["recording_path"].status == "WARN"
    assert res_dict["recording_format"].status == "FAIL"
    assert "Hybrid MP4" in res_dict["recording_format"].fix_instruction
    assert res_dict["encoder"].status == "FAIL"
    assert "NVENC" in res_dict["encoder"].fix_instruction
    assert res_dict["bitrate"].status == "PASS"
    assert res_dict["file_splitting"].status == "FAIL"
    assert "Split by Time" in res_dict["file_splitting"].fix_instruction


def test_adaptive_bitrate_queue_integration(tmp_path):
    storage = Storage(db_path=tmp_path / "obs_int.db")
    config = AppConfig(
        queue_threshold_lower_mb=300.0,
        queue_threshold_critical_mb=600.0,
        queue_threshold_hardcap_mb=850.0,
        target_bitrate_kbps=10000,
        min_bitrate_kbps=2500,
    )
    obs_client = MagicMock(spec=ObsClient)

    def guard_action_handler(decision):
        if decision.should_pause_recording:
            obs_client.pause_recording()
        elif decision.should_resume_recording:
            obs_client.resume_recording()
        else:
            obs_client.set_recording_bitrate(decision.recommended_bitrate_kbps)

    guard = QueueGuard(storage=storage, config=config, on_guard_action=guard_action_handler)

    # 1. Elevate queue -> Bitrate drops
    with patch.object(guard, "get_free_disk_space_mb", return_value=5000.0):
        with patch.object(storage, "get_queue_stats", return_value={"pending_mb": 350.0}):
            dec = guard.evaluate()
            assert dec.level == GuardLevel.ELEVATED
            obs_client.set_recording_bitrate.assert_called_with(dec.recommended_bitrate_kbps)

        # 2. Hard Cap reached -> Triggers pause
        with patch.object(storage, "get_queue_stats", return_value={"pending_mb": 900.0}):
            dec2 = guard.evaluate()
            assert dec2.level == GuardLevel.HARD_CAP
            obs_client.pause_recording.assert_called_once()


def test_auto_configure_obs(obs_mock_setup):
    client, config = obs_mock_setup
    mock_req_client = MagicMock()
    client._client = mock_req_client
    client.state.is_connected = True

    mock_enc_param = MagicMock()
    mock_enc_param.parameter_value = "obs_x264"
    mock_req_client.get_profile_parameter.return_value = mock_enc_param

    success, actions = client.auto_configure_obs(
        recording_dir="C:/Videos/OBS_Rec", target_bitrate_kbps=12000
    )
    assert success is True
    assert len(actions) >= 4
    # Check that settings were pushed
    mock_req_client.set_profile_parameter.assert_any_call("Output", "Mode", "Advanced")
    mock_req_client.set_profile_parameter.assert_any_call("AdvOut", "RecSplitFile", "true")
    mock_req_client.set_record_directory.assert_called_once()


def test_obs_connection_auth_required_error(obs_mock_setup):
    client, _ = obs_mock_setup
    with patch("obsws_python.ReqClient", side_effect=Exception("authentication enabled but no password provided")):
        success, msg = client.connect_with_status()
        assert success is False
        assert client.last_auth_error == "AUTH_REQUIRED_NO_PASSWORD"
        assert "uncheck 'Enable Authentication'" in msg
        assert "Show Connect Info" in msg


def test_obs_connection_auth_invalid_password(obs_mock_setup):
    client, _ = obs_mock_setup
    with patch("obsws_python.ReqClient", side_effect=Exception("Authentication failed: code 4009")):
        success, msg = client.connect_with_status()
        assert success is False
        assert client.last_auth_error == "AUTH_INVALID_PASSWORD"
        assert "password incorrect" in msg


def test_setup_check_shows_auth_error(obs_mock_setup):
    client, _ = obs_mock_setup
    client.disconnect()
    client.last_auth_error = "AUTH_REQUIRED_NO_PASSWORD"

    with patch.object(client, "connect", return_value=False):
        results = client.run_setup_check()
        conn_check = next(r for r in results if r.key == "connection")
        assert conn_check.status == "FAIL"
        assert "Authentication" in conn_check.title
        assert "uncheck 'Enable Authentication'" in conn_check.fix_instruction


def test_active_segment_splitter_triggers_split(obs_mock_setup):
    import time
    client, config = obs_mock_setup
    config.segment_length_sec = 30
    mock_req_client = MagicMock()
    client._client = mock_req_client
    client.state.is_connected = True

    rec_status = MagicMock()
    rec_status.output_active = True
    rec_status.output_paused = False
    rec_status.output_timecode = "00:00:31"
    rec_status.output_bytes = 50000000
    mock_req_client.get_record_status.return_value = rec_status

    # Initial call sets start time
    client.update_state_now()
    assert client.state.is_recording is True
    assert client.segment_split_count == 0

    # Advance time beyond 30s
    client._last_split_time = time.time() - 35.0
    client.update_state_now()

    # Verify split_record_file was invoked and count incremented
    mock_req_client.split_record_file.assert_called_once()
    assert client.segment_split_count == 1



