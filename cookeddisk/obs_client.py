"""OBS Studio WebSocket v5 client, setup validator, and adaptive bitrate controller."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

try:
    import obsws_python as obs
except ImportError:
    obs = None

from cookeddisk.config import AppConfig
from cookeddisk.logger import get_logger

logger = get_logger("obs_client")


def detect_system_gpu_encoder() -> str:
    """Detects available GPU hardware encoder on Windows (Intel QuickSync, NVIDIA NVENC, or AMD AMF)."""
    try:
        import subprocess

        out = subprocess.check_output(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name",
            ],
            text=True,
            timeout=3,
        ).lower()
        if "nvidia" in out or "geforce" in out or "quadro" in out:
            return "jim_nvenc"
        if "intel" in out:
            return "obs_qsv11"
        if "amd" in out or "radeon" in out:
            return "amd_amf_h264"
    except Exception:
        pass
    return "obs_qsv11"



@dataclass
class SetupCheckItem:
    key: str
    title: str
    status: str  # "PASS", "FAIL", "WARN"
    current_value: str
    fix_instruction: str


@dataclass
class ObsState:
    is_connected: bool = False
    is_recording: bool = False
    is_paused: bool = False
    record_timecode: str = "00:00:00"
    record_bytes: int = 0
    current_bitrate_kbps: int = 10000
    recording_directory: str = ""


class ObsClient:
    """
    Manages connection to OBS Studio via WebSocket v5 (obsws-python).
    Monitors recording state, adjusts encoder bitrate, and executes setup diagnostics.
    """

    def __init__(self, config: Optional[AppConfig] = None):
        self.config = config or AppConfig.load()
        self._client: Optional[Any] = None
        self._lock = threading.RLock()
        self.state = ObsState()
        self._poll_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self.on_state_change: Optional[Callable[[ObsState], None]] = None
        self.last_auth_error: Optional[str] = None
        self.last_error_message: Optional[str] = None
        self._last_failed_attempt_time: float = 0.0
        self._auth_backoff_sec: float = 25.0

        # Automated segment splitter tracking
        self._last_split_time: float = 0.0
        self._recording_active_since: float = 0.0
        self.segment_split_count: int = 0

    def connect_with_status(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        password: Optional[str] = None,
    ) -> tuple[bool, str]:
        if obs is None:
            msg = "obsws-python library is not installed."
            logger.error(msg)
            return False, msg

        h = host or self.config.obs_host
        p = port or self.config.obs_port
        pw = password if password is not None else self.config.obs_password

        with self._lock:
            self.disconnect()
            try:
                logger.info(f"Connecting to OBS WebSocket at {h}:{p}...")
                self._client = obs.ReqClient(host=h, port=p, password=pw, timeout=4)
                self.state.is_connected = True
                self.last_auth_error = None
                self.last_error_message = None
                self._last_failed_attempt_time = 0.0
                logger.info("Connected to OBS WebSocket successfully.")
                self.update_state_now()
                return True, f"Connected to OBS WebSocket at {h}:{p} successfully."
            except Exception as e:
                # Clean up dangling socket if ReqClient failed during handshake/auth
                import sys

                tb = sys.exc_info()[2]
                while tb:
                    frame = tb.tb_frame
                    if "self" in frame.f_locals and hasattr(frame.f_locals["self"], "base_client"):
                        try:
                            frame.f_locals["self"].base_client.ws.close()
                        except Exception:
                            pass
                        break
                    tb = tb.tb_next

                self._client = None
                self.state.is_connected = False
                self._last_failed_attempt_time = time.time()

                err_str = str(e)
                err_lower = err_str.lower()

                if "authentication enabled but no password provided" in err_lower or (
                    "authentication" in err_lower and "no password" in err_lower
                ):
                    self.last_auth_error = "AUTH_REQUIRED_NO_PASSWORD"
                    msg = (
                        "OBS WebSocket has authentication enabled, but no password is configured in CookedDisk.\n\n"
                        "To fix this, choose one of these options:\n"
                        "1. (Recommended) In OBS, click Tools -> WebSocket Server Settings and uncheck 'Enable Authentication'.\n"
                        "2. Or in OBS, click Tools -> WebSocket Server Settings -> Show Connect Info -> copy Server Password and paste it into CookedDisk Settings."
                    )
                elif "authentication failed" in err_lower or "4009" in err_str:
                    self.last_auth_error = "AUTH_INVALID_PASSWORD"
                    msg = (
                        "OBS WebSocket authentication failed (password incorrect).\n\n"
                        "To fix this:\n"
                        "1. In OBS, go to Tools -> WebSocket Server Settings -> Show Connect Info.\n"
                        "2. Copy Server Password and paste it into CookedDisk Settings (or uncheck 'Enable Authentication')."
                    )
                elif (
                    "connection refused" in err_lower
                    or "10061" in err_str
                    or "timed out" in err_lower
                    or "timeout" in err_lower
                ):
                    self.last_auth_error = "CONNECTION_REFUSED"
                    msg = (
                        f"Could not connect to OBS WebSocket at {h}:{p}.\n\n"
                        "Please verify that OBS Studio is running and Tools -> WebSocket Server Settings has 'Enable WebSocket server' checked."
                    )
                else:
                    self.last_auth_error = "CONNECTION_ERROR"
                    msg = f"Could not connect to OBS WebSocket at {h}:{p}: {err_str}"

                self.last_error_message = msg
                logger.warning(f"OBS connection failed: {msg}")
                return False, msg

    def connect(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        password: Optional[str] = None,
    ) -> bool:
        success, _ = self.connect_with_status(host=host, port=port, password=password)
        return success

    def reset_auth_backoff(self) -> None:
        """Resets failed attempt timestamp so polling or next action can immediately retry."""
        self._last_failed_attempt_time = 0.0
        self.last_auth_error = None
        self.last_error_message = None

    def disconnect(self) -> None:
        with self._lock:
            if self._client:
                try:
                    self._client.base_client.ws.close()
                except Exception:
                    pass
                self._client = None
            self.state.is_connected = False
            self.state.is_recording = False
            self.state.is_paused = False

    def is_connected(self) -> bool:
        with self._lock:
            return bool(self._client and self.state.is_connected)

    def update_state_now(self) -> ObsState:
        with self._lock:
            if not self._client:
                self.state.is_connected = False
                return self.state

            try:
                rec_status = self._client.get_record_status()
                was_recording = self.state.is_recording
                is_recording = getattr(rec_status, "output_active", False)
                self.state.is_recording = is_recording
                self.state.is_paused = getattr(rec_status, "output_paused", False)
                self.state.record_timecode = getattr(rec_status, "output_timecode", "00:00:00")
                self.state.record_bytes = getattr(rec_status, "output_bytes", 0)

                try:
                    rec_dir = self._client.get_record_directory()
                    self.state.recording_directory = getattr(rec_dir, "record_directory", "")
                except Exception:
                    pass

                self.state.is_connected = True

                # Automated 30-Second Segment Splitter
                now = time.time()
                if is_recording:
                    if not was_recording:
                        self._recording_active_since = now
                        self._last_split_time = now
                        self.segment_split_count = 0
                        logger.info(
                            f"OBS recording started. Automated segment splitter engaged "
                            f"(cutting every {self.config.segment_length_sec}s)."
                        )
                    elif not self.state.is_paused:
                        elapsed = now - self._last_split_time
                        if elapsed >= self.config.segment_length_sec:
                            if self.split_recording():
                                self.segment_split_count += 1
                                logger.info(
                                    f"Automated split #{self.segment_split_count} triggered "
                                    f"after {elapsed:.1f}s via obs-websocket (SplitRecordFile)."
                                )
                elif was_recording:
                    logger.info(
                        f"OBS recording stopped. Total segments split during session: {self.segment_split_count}."
                    )
            except Exception as e:
                logger.debug(f"Error updating OBS state: {e}")
                self.state.is_connected = False
                self._client = None

            if self.on_state_change:
                try:
                    self.on_state_change(self.state)
                except Exception:
                    pass

            return self.state

    def split_recording(self) -> bool:
        """Triggers OBS Studio to split active recording into a new segment file on the fly."""
        with self._lock:
            if not self.is_connected() or not self.state.is_recording:
                return False
            try:
                if hasattr(self._client, "split_record_file"):
                    self._client.split_record_file()
                elif hasattr(self._client, "send"):
                    self._client.send("SplitRecordFile")
                self._last_split_time = time.time()
                return True
            except Exception as e:
                logger.warning(f"Failed to split OBS recording file: {e}")
                return False

    def start_polling(self, interval_sec: float = 1.0) -> None:
        """Polls OBS status periodically to track active/paused recording and trigger segment splits."""
        with self._lock:
            if self._poll_thread and self._poll_thread.is_alive():
                return
            self._stop_event.clear()
            self._poll_thread = threading.Thread(
                target=self._poll_loop, args=(interval_sec,), name="ObsPollThread", daemon=True
            )
            self._poll_thread.start()

    def stop_polling(self) -> None:
        self._stop_event.set()
        if self._poll_thread and self._poll_thread.is_alive():
            self._poll_thread.join(timeout=2.0)
        self._poll_thread = None

    def _poll_loop(self, interval_sec: float) -> None:
        while not self._stop_event.is_set():
            if not self.is_connected() and self.config.obs_auto_connect:
                # If authentication failed (missing or incorrect password), do not spam OBS every 2s.
                # Back off to avoid flooding OBS logs with 'unidentified Kick' messages.
                now = time.time()
                is_auth_error = self.last_auth_error in (
                    "AUTH_REQUIRED_NO_PASSWORD",
                    "AUTH_INVALID_PASSWORD",
                )
                if not is_auth_error or (now - self._last_failed_attempt_time >= self._auth_backoff_sec):
                    self.connect()
            else:
                self.update_state_now()
            self._stop_event.wait(interval_sec)

    def pause_recording(self) -> bool:
        with self._lock:
            if not self.is_connected():
                return False
            try:
                self._client.pause_record()
                logger.info("Paused OBS recording.")
                self.update_state_now()
                return True
            except Exception as e:
                logger.warning(f"Failed to pause OBS recording: {e}")
                return False

    def resume_recording(self) -> bool:
        with self._lock:
            if not self.is_connected():
                return False
            try:
                self._client.resume_record()
                logger.info("Resumed OBS recording.")
                self.update_state_now()
                return True
            except Exception as e:
                logger.warning(f"Failed to resume OBS recording: {e}")
                return False

    def set_recording_bitrate(self, bitrate_kbps: int) -> bool:
        """
        Dynamically adjusts encoder bitrate via obs-websocket.
        Supports both Advanced and Simple output modes across NVENC, AMF, QuickSync, and x264.
        """
        with self._lock:
            if not self.is_connected():
                return False

            success = False
            # 1. Try SetOutputSettings for adv_file_output or simple_file_output
            for output_name in ("adv_file_output", "simple_file_output", "adv_stream_output"):
                try:
                    self._client.set_output_settings(
                        output_name,
                        {"bitrate": bitrate_kbps, "video_bitrate": bitrate_kbps},
                    )
                    success = True
                except Exception:
                    pass

            # 2. Try updating profile parameter for Advanced & Simple outputs
            for cat, param in [
                ("SimpleOutput", "VBitrate"),
                ("AdvOut", "RecBitrate"),
                ("AdvOut", "FFVBitrate"),
                ("AdvOut", "Track1Bitrate"),
            ]:
                try:
                    self._client.set_profile_parameter(cat, param, str(bitrate_kbps))
                    success = True
                except Exception:
                    pass

            if success:
                self.state.current_bitrate_kbps = bitrate_kbps
                logger.info(f"Applied OBS bitrate change: {bitrate_kbps} kbps")
            else:
                logger.debug(f"Bitrate change to {bitrate_kbps} kbps could not be applied to active output.")
            return success

    def auto_configure_obs(
        self,
        recording_dir: Optional[str] = None,
        target_bitrate_kbps: Optional[int] = None,
    ) -> Tuple[bool, List[str]]:
        """
        Automatically pushes optimal configuration to OBS Studio over WebSocket v5:
        1. Set Output Mode to Advanced
        2. Set Recording Format to Hybrid MP4 / MKV
        3. Enable Automatic File Splitting by Time (30 seconds)
        4. Configure Target Bitrate (default 10,000 kbps)
        5. Synchronize Recording Directory to CookedDisk's watched folder
        6. Select Hardware GPU Encoder if available
        Returns (success: bool, actions_applied: List[str]).
        """
        with self._lock:
            if not self.is_connected():
                success, msg = self.connect_with_status()
                if not success:
                    return False, [
                        f"Cannot connect to OBS WebSocket: {self.last_error_message or msg}"
                    ]

            actions: List[str] = []
            target_br = target_bitrate_kbps or self.config.target_bitrate_kbps
            target_dir = recording_dir or self.config.obs_recording_dir

            # 1. Output Mode: Advanced
            try:
                self._client.set_profile_parameter("Output", "Mode", "Advanced")
                actions.append("Set Output Mode to Advanced")
            except Exception as e:
                logger.debug(f"Could not set Output Mode: {e}")

            # 2. Recording Format: Hybrid MP4 / MKV
            fmt_set = False
            for cat, param, val in [
                ("AdvOut", "RecFormat2", "fragmented_mp4"),
                ("AdvOut", "RecFormat", "fragmented_mp4"),
                ("SimpleOutput", "RecFormat", "fragmented_mp4"),
            ]:
                try:
                    self._client.set_profile_parameter(cat, param, val)
                    fmt_set = True
                except Exception:
                    pass

            if fmt_set:
                actions.append("Configured safe crash-resistant format (Hybrid MP4)")

            # 3. Automatic File Splitting fallback configuration (1 minute in OBS GUI)
            split_set = False
            for cat, param, val in [
                ("AdvOut", "RecSplitFile", "true"),
                ("AdvOut", "RecSplitFileType", "Time"),
                ("AdvOut", "RecSplitFileTime", "1"),
                ("AdvOut", "RecSplitType", "time"),
                ("AdvOut", "RecSplitTime", "1"),
            ]:
                try:
                    self._client.set_profile_parameter(cat, param, val)
                    split_set = True
                except Exception:
                    pass

            if split_set:
                actions.append("Enabled Automatic File Splitting (OBS fallback 1m, CookedDisk active 30s)")

            # 4. Hardware Encoder selection (prioritize detected system GPU)
            preferred_gpu = detect_system_gpu_encoder()
            candidate_encoders = [preferred_gpu]
            for c in ("obs_qsv11", "jim_nvenc", "amd_amf_h264", "ffmpeg_nvenc"):
                if c not in candidate_encoders:
                    candidate_encoders.append(c)

            enc_applied = False
            for candidate in candidate_encoders:
                try:
                    self._client.set_profile_parameter("AdvOut", "RecEncoder", candidate)
                    self._client.set_profile_parameter("AdvOut", "Encoder", candidate)
                    self._client.set_profile_parameter("SimpleOutput", "StreamEncoder", candidate)
                    self._client.set_profile_parameter("SimpleOutput", "RecEncoder", candidate)
                    actions.append(f"Configured Hardware Video Encoder ({candidate})")
                    enc_applied = True
                    break
                except Exception:
                    pass

            # 5. Bitrate & Quality Configuration (10,000 kbps HQ)
            self.set_recording_bitrate(target_br)
            for cat, param, val in [
                ("AdvOut", "RecRateControl", "CBR"),
                ("AdvOut", "RecBitrate", str(target_br)),
                ("AdvOut", "Track1Bitrate", str(target_br)),
                ("SimpleOutput", "VBitrate", str(target_br)),
                ("SimpleOutput", "RecQuality", "HQ"),
            ]:
                try:
                    self._client.set_profile_parameter(cat, param, val)
                except Exception:
                    pass
            actions.append(f"Configured High-Quality Bitrate to {target_br} kbps (CBR)")

            # 6. Synchronize recording path
            if target_dir:
                try:
                    p = str(Path(target_dir).resolve())
                    self._client.set_record_directory(p)
                    actions.append(f"Synchronized OBS recording directory to: {p}")
                except Exception as e:
                    logger.debug(f"Could not set record directory: {e}")

            logger.info(f"Auto-configured OBS with {len(actions)} actions.")
            return True, actions

    def run_setup_check(self) -> List[SetupCheckItem]:
        """
        Runs comprehensive setup check against OBS Studio.
        Reports pass/fail/warn for each item with plain-language fix instructions.
        """
        results: List[SetupCheckItem] = []

        # 1. Connection check
        connected = self.is_connected()
        if not connected:
            connected = self.connect()

        if not connected:
            if self.last_auth_error == "AUTH_REQUIRED_NO_PASSWORD":
                results.append(
                    SetupCheckItem(
                        key="connection",
                        title="OBS WebSocket Authentication",
                        status="FAIL",
                        current_value="Authentication enabled in OBS, but no password configured in CookedDisk",
                        fix_instruction=(
                            "In OBS Studio -> Tools -> WebSocket Server Settings: either uncheck 'Enable Authentication' "
                            "(recommended for local use) or click 'Show Connect Info', copy Server Password, and paste it into CookedDisk Settings."
                        ),
                    )
                )
            elif self.last_auth_error == "AUTH_INVALID_PASSWORD":
                results.append(
                    SetupCheckItem(
                        key="connection",
                        title="OBS WebSocket Authentication",
                        status="FAIL",
                        current_value="Authentication failed (password incorrect)",
                        fix_instruction=(
                            "The password in CookedDisk Settings does not match OBS Studio. "
                            "In OBS Studio -> Tools -> WebSocket Server Settings -> Show Connect Info, copy Server Password into CookedDisk Settings."
                        ),
                    )
                )
            else:
                results.append(
                    SetupCheckItem(
                        key="connection",
                        title="OBS WebSocket Connection",
                        status="FAIL",
                        current_value=f"Disconnected ({self.config.obs_host}:{self.config.obs_port})",
                        fix_instruction=(
                            "In OBS Studio, click Tools -> WebSocket Server Settings. "
                            "Check 'Enable WebSocket server', verify Server Port (default 4455). "
                            "If 'Enable Authentication' is checked, copy Server Password into CookedDisk Settings."
                        ),
                    )
                )
            # Cannot check remaining items if not connected
            for key, title in [
                ("recording_path", "Recording Folder"),
                ("recording_format", "Recording Format"),
                ("encoder", "Hardware Video Encoder"),
                ("bitrate", "Bitrate Configuration"),
                ("file_splitting", "Automatic File Splitting"),
            ]:
                results.append(
                    SetupCheckItem(
                        key=key,
                        title=title,
                        status="FAIL",
                        current_value="Unavailable (OBS not connected)",
                        fix_instruction="Connect to OBS via WebSocket first to verify this setting.",
                    )
                )
            return results

        results.append(
            SetupCheckItem(
                key="connection",
                title="OBS WebSocket Connection",
                status="PASS",
                current_value=f"Connected to {self.config.obs_host}:{self.config.obs_port}",
                fix_instruction="No action required.",
            )
        )

        # 2. Recording path
        obs_rec_dir = ""
        try:
            res = self._client.get_record_directory()
            obs_rec_dir = getattr(res, "record_directory", "")
        except Exception:
            pass

        app_dir = self.config.obs_recording_dir.strip()
        if obs_rec_dir and app_dir and Path(obs_rec_dir).resolve() == Path(app_dir).resolve():
            results.append(
                SetupCheckItem(
                    key="recording_path",
                    title="Recording Folder",
                    status="PASS",
                    current_value=obs_rec_dir,
                    fix_instruction="No action required. Folder paths match.",
                )
            )
        elif obs_rec_dir:
            results.append(
                SetupCheckItem(
                    key="recording_path",
                    title="Recording Folder",
                    status="WARN",
                    current_value=f"OBS: {obs_rec_dir} vs App: {app_dir or 'Not set'}",
                    fix_instruction=(
                        f"Set CookedDisk's watched recording folder to '{obs_rec_dir}' "
                        "in Settings so CookedDisk knows where OBS outputs files."
                    ),
                )
            )
        else:
            results.append(
                SetupCheckItem(
                    key="recording_path",
                    title="Recording Folder",
                    status="WARN",
                    current_value=app_dir or "Not configured",
                    fix_instruction="Ensure the recording folder configured in OBS matches CookedDisk Settings.",
                )
            )

        # 3. Recording Format (Hybrid MP4 or MKV)
        rec_format = ""
        try:
            param = self._client.get_profile_parameter("AdvOut", "RecFormat")
            rec_format = getattr(param, "parameter_value", "")
        except Exception:
            pass

        if not rec_format:
            try:
                param = self._client.get_profile_parameter("AdvOut", "RecFormat2")
                rec_format = getattr(param, "parameter_value", "")
            except Exception:
                pass

        clean_fmt = rec_format.lower().strip()
        if "hybrid" in clean_fmt or "fragmented" in clean_fmt or clean_fmt in ("mkv", "matroska"):
            results.append(
                SetupCheckItem(
                    key="recording_format",
                    title="Recording Format",
                    status="PASS",
                    current_value=f"Safe crash-resistant format ({rec_format or 'Hybrid MP4/MKV'})",
                    fix_instruction="No action required.",
                )
            )
        elif clean_fmt in ("mp4", "mov"):
            results.append(
                SetupCheckItem(
                    key="recording_format",
                    title="Recording Format",
                    status="FAIL",
                    current_value=rec_format or "Standard MP4",
                    fix_instruction=(
                        "In OBS Studio, open Settings -> Output -> Output Mode: Advanced -> Recording tab. "
                        "Change Recording Format to 'Hybrid MP4' (or 'MKV'). "
                        "Standard MP4 files become completely unplayable if OBS closes unexpectedly."
                    ),
                )
            )
        else:
            results.append(
                SetupCheckItem(
                    key="recording_format",
                    title="Recording Format",
                    status="WARN",
                    current_value=rec_format or "Unknown",
                    fix_instruction=(
                        "In OBS Studio Settings -> Output -> Recording, ensure Recording Format is set to "
                        "'Hybrid MP4' or 'MKV'."
                    ),
                )
            )

        # 4. Hardware Encoder (NVENC / AMF / QuickSync)
        encoder_name = ""
        try:
            param = self._client.get_profile_parameter("AdvOut", "RecEncoder")
            encoder_name = getattr(param, "parameter_value", "")
        except Exception:
            pass

        if not encoder_name or encoder_name.lower() in ("none", "(use stream encoder)"):
            try:
                param = self._client.get_profile_parameter("AdvOut", "Encoder")
                encoder_name = getattr(param, "parameter_value", "")
            except Exception:
                pass

        clean_enc = encoder_name.lower().strip()
        is_hw = any(k in clean_enc for k in ["nvenc", "amf", "qsv", "quicksync", "vaapi", "hardware"])
        is_sw = any(k in clean_enc for k in ["x264", "svt-av1", "aom", "software", "none", ""])

        if is_hw:
            results.append(
                SetupCheckItem(
                    key="encoder",
                    title="Hardware Video Encoder",
                    status="PASS",
                    current_value=f"Hardware Accelerated ({encoder_name})",
                    fix_instruction="No action required. Hardware GPU encoder active.",
                )
            )
        elif is_sw:
            sys_gpu = detect_system_gpu_encoder()
            results.append(
                SetupCheckItem(
                    key="encoder",
                    title="Hardware Video Encoder",
                    status="FAIL",
                    current_value=f"Software CPU encoder ({encoder_name or 'x264'})",
                    fix_instruction=(
                        f"In OBS Studio Settings -> Output -> Recording tab, change Video Encoder from software (x264) "
                        f"to a hardware encoder (NVIDIA NVENC, AMD AMF, or Intel QuickSync '{sys_gpu}') "
                        f"or click 'Auto-Configure OBS' on this screen."
                    ),
                )
            )
        else:
            results.append(
                SetupCheckItem(
                    key="encoder",
                    title="Hardware Video Encoder",
                    status="PASS" if encoder_name else "WARN",
                    current_value=encoder_name or "Standard Encoder",
                    fix_instruction=(
                        "In OBS Studio Settings -> Output -> Recording, choose a GPU hardware encoder "
                        "(NVENC, AMF, or QuickSync) if available."
                    ),
                )
            )

        # 5. Bitrate (~10-12 Mbps)
        bitrate_str = ""
        try:
            param = self._client.get_profile_parameter("AdvOut", "RecBitrate")
            bitrate_str = getattr(param, "parameter_value", "")
        except Exception:
            pass

        if not bitrate_str:
            try:
                param = self._client.get_profile_parameter("SimpleOutput", "VBitrate")
                bitrate_str = getattr(param, "parameter_value", "")
            except Exception:
                pass

        try:
            bitrate_val = int(bitrate_str)
        except ValueError:
            bitrate_val = 0

        if 7000 <= bitrate_val <= 15000:
            results.append(
                SetupCheckItem(
                    key="bitrate",
                    title="Bitrate Configuration",
                    status="PASS",
                    current_value=f"{bitrate_val} kbps ({bitrate_val/1000:.1f} Mbps)",
                    fix_instruction="No action required. Bitrate is optimal for 1080p60 gameplay.",
                )
            )
        elif bitrate_val > 15000:
            results.append(
                SetupCheckItem(
                    key="bitrate",
                    title="Bitrate Configuration",
                    status="WARN",
                    current_value=f"{bitrate_val} kbps (Very high)",
                    fix_instruction=(
                        f"Your bitrate ({bitrate_val} kbps) will generate ~{bitrate_val*0.45/1000:.1f} GB per hour. "
                        "Because local disk space is limited to ~1 GB, recommend 10,000-12,000 kbps."
                    ),
                )
            )
        elif bitrate_val > 0:
            results.append(
                SetupCheckItem(
                    key="bitrate",
                    title="Bitrate Configuration",
                    status="WARN",
                    current_value=f"{bitrate_val} kbps (Low)",
                    fix_instruction="Bitrate is below 7000 kbps. Video quality may look pixelated during fast motion.",
                )
            )
        else:
            results.append(
                SetupCheckItem(
                    key="bitrate",
                    title="Bitrate Configuration",
                    status="PASS",
                    current_value=f"Target: {self.config.target_bitrate_kbps} kbps",
                    fix_instruction="Ensure recording bitrate is set to ~10,000-12,000 kbps in OBS Settings.",
                )
            )

        # 6. Automatic File Splitting (Automated WebSocket 30s + OBS fallback)
        split_enabled = ""
        try:
            param_en = self._client.get_profile_parameter("AdvOut", "RecSplitFile")
            split_enabled = getattr(param_en, "parameter_value", "")
        except Exception:
            pass

        if split_enabled.lower() in ("true", "1", "yes"):
            results.append(
                SetupCheckItem(
                    key="file_splitting",
                    title="Automatic File Splitting",
                    status="PASS",
                    current_value="Automated 30s splitter active (via WebSocket) + OBS Split",
                    fix_instruction="No action required. CookedDisk automatically splits recordings every 30 seconds into Google Drive.",
                )
            )
        else:
            results.append(
                SetupCheckItem(
                    key="file_splitting",
                    title="Automatic File Splitting",
                    status="FAIL",
                    current_value="OBS Automatic Splitting disabled in profile",
                    fix_instruction=(
                        "In OBS Studio, open Settings -> Output -> Output Mode: Advanced -> Recording tab. "
                        "Check 'Automatic File Splitting' and set Split by Time for local fallback, "
                        "or click 'Auto-Configure OBS' on this screen."
                    ),
                )
            )

        return results
