"""Queue and disk guard with hysteresis to prevent bitrate flapping and disk overflow."""

from __future__ import annotations

import enum
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from cookeddisk.config import AppConfig
from cookeddisk.logger import get_logger
from cookeddisk.storage import Storage

logger = get_logger("queue_guard")


class GuardLevel(enum.Enum):
    NORMAL = "NORMAL"        # Queue <= 300 MB (Full target bitrate)
    ELEVATED = "ELEVATED"    # Queue > 300 MB (Reduced bitrate)
    CRITICAL = "CRITICAL"    # Queue > 600 MB (Minimum bitrate or pause)
    HARD_CAP = "HARD_CAP"    # Queue > 850 MB or free disk < safety margin (Forced pause)


@dataclass
class GuardDecision:
    level: GuardLevel
    recommended_bitrate_kbps: int
    should_pause_recording: bool
    should_resume_recording: bool
    reason: str
    pending_mb: float
    free_disk_mb: float


class QueueGuard:
    """
    Monitors queue size and local free disk space.
    Calculates bitrate adjustments using hysteresis so the bitrate does not flap.
    Guarantees the local disk never fills up.
    """

    def __init__(
        self,
        storage: Storage,
        config: Optional[AppConfig] = None,
        on_guard_action: Optional[Callable[[GuardDecision], None]] = None,
    ):
        self.storage = storage
        self.config = config or AppConfig.load()
        self.on_guard_action = on_guard_action

        # State tracking for hysteresis
        self.current_level: GuardLevel = GuardLevel.NORMAL
        self.current_bitrate_kbps: int = self.config.target_bitrate_kbps
        self.is_recording_paused_by_guard: bool = False

    def get_free_disk_space_mb(self, check_path: Optional[Path | str] = None) -> float:
        """Returns free disk space in megabytes for the drive containing check_path."""
        p = Path(check_path) if check_path else (Path(self.config.obs_recording_dir) if self.config.obs_recording_dir else Path.home())
        if not p.exists():
            p = p.parent if p.parent.exists() else Path.home()
        try:
            usage = shutil.disk_usage(p)
            return usage.free / (1024 * 1024)
        except Exception as e:
            logger.warning(f"Failed to check disk usage: {e}")
            return 999999.0

    def evaluate(self, force_check_path: Optional[Path | str] = None) -> GuardDecision:
        """
        Evaluate queue size and disk headroom, applying hysteresis logic.
        """
        stats = self.storage.get_queue_stats()
        pending_mb = stats["pending_mb"]
        free_disk_mb = self.get_free_disk_space_mb(force_check_path)

        # Config thresholds
        t_lower = self.config.queue_threshold_lower_mb        # default 300 MB
        t_critical = self.config.queue_threshold_critical_mb  # default 600 MB
        t_hardcap = self.config.queue_threshold_hardcap_mb    # default 850 MB

        # Hysteresis recovery thresholds
        rec_normal = self.config.hysteresis_recovery_mb           # default 200 MB
        rec_elevated = self.config.hysteresis_critical_recovery_mb # default 450 MB

        # Hard disk absolute safety floor: if physical free space on disk is < 150 MB, emergency pause!
        disk_critical_floor_mb = 150.0

        prev_level = self.current_level
        new_level = prev_level
        should_pause = False
        should_resume = False
        reason = ""

        # State transition with hysteresis
        if pending_mb >= t_hardcap or free_disk_mb <= disk_critical_floor_mb:
            new_level = GuardLevel.HARD_CAP
            reason = (
                f"Emergency hard cap reached: pending={pending_mb:.1f}MB (cap={t_hardcap}MB), "
                f"free disk={free_disk_mb:.1f}MB (floor={disk_critical_floor_mb}MB)."
            )
            should_pause = True

        elif prev_level == GuardLevel.HARD_CAP:
            # Must drop below critical threshold to leave HARD_CAP
            if pending_mb < t_critical and free_disk_mb > (disk_critical_floor_mb + 100):
                new_level = GuardLevel.CRITICAL
                reason = f"Queue reduced from hard cap to {pending_mb:.1f}MB. Resuming recording at minimum bitrate."
                should_resume = self.is_recording_paused_by_guard
            else:
                new_level = GuardLevel.HARD_CAP
                reason = f"Holding emergency pause: pending={pending_mb:.1f}MB."
                should_pause = True

        elif prev_level == GuardLevel.CRITICAL:
            # Hysteresis: only drop back to ELEVATED if pending < rec_elevated (450 MB)
            if pending_mb < rec_elevated:
                new_level = GuardLevel.ELEVATED
                reason = f"Queue drained to {pending_mb:.1f}MB (<{rec_elevated}MB). Stepping up to reduced bitrate."
                should_resume = self.is_recording_paused_by_guard
            else:
                new_level = GuardLevel.CRITICAL
                reason = f"Remaining in critical guard: pending={pending_mb:.1f}MB."

        elif prev_level == GuardLevel.ELEVATED:
            # Escalate to CRITICAL if pending >= t_critical (600 MB)
            if pending_mb >= t_critical:
                new_level = GuardLevel.CRITICAL
                reason = f"Queue exceeded critical limit: pending={pending_mb:.1f}MB (>={t_critical}MB)."
            # Hysteresis: only drop back to NORMAL if pending < rec_normal (200 MB)
            elif pending_mb < rec_normal:
                new_level = GuardLevel.NORMAL
                reason = f"Queue fully drained to {pending_mb:.1f}MB (<{rec_normal}MB). Restoring full bitrate."
            else:
                new_level = GuardLevel.ELEVATED
                reason = f"Holding reduced bitrate (hysteresis): pending={pending_mb:.1f}MB."

        else:  # prev_level == GuardLevel.NORMAL
            # Escalate
            if pending_mb >= t_critical:
                new_level = GuardLevel.CRITICAL
                reason = f"Queue spiked to critical: pending={pending_mb:.1f}MB."
            elif pending_mb >= t_lower:
                new_level = GuardLevel.ELEVATED
                reason = f"Queue elevated: pending={pending_mb:.1f}MB (>={t_lower}MB). Lowering encoder bitrate."
            else:
                new_level = GuardLevel.NORMAL
                reason = f"Normal operation: pending={pending_mb:.1f}MB, free disk={free_disk_mb:.1f}MB."

        # Compute recommended bitrate for new_level
        target_br = self.config.target_bitrate_kbps
        min_br = self.config.min_bitrate_kbps
        mid_br = int(min_br + (target_br - min_br) * 0.5)

        rec_bitrate = target_br
        if new_level == GuardLevel.NORMAL:
            rec_bitrate = target_br
        elif new_level == GuardLevel.ELEVATED:
            rec_bitrate = mid_br
        elif new_level == GuardLevel.CRITICAL:
            rec_bitrate = min_br
            if self.config.critical_action == "pause_recording":
                should_pause = True
        elif new_level == GuardLevel.HARD_CAP:
            rec_bitrate = min_br
            should_pause = True

        if should_pause:
            self.is_recording_paused_by_guard = True
        elif should_resume:
            self.is_recording_paused_by_guard = False

        if new_level != prev_level or rec_bitrate != self.current_bitrate_kbps:
            logger.info(
                f"QueueGuard transition: {prev_level.value} -> {new_level.value} "
                f"| Bitrate: {self.current_bitrate_kbps} -> {rec_bitrate} kbps | {reason}"
            )

        self.current_level = new_level
        self.current_bitrate_kbps = rec_bitrate

        decision = GuardDecision(
            level=new_level,
            recommended_bitrate_kbps=rec_bitrate,
            should_pause_recording=should_pause,
            should_resume_recording=should_resume,
            reason=reason,
            pending_mb=pending_mb,
            free_disk_mb=free_disk_mb,
        )

        if self.on_guard_action:
            try:
                self.on_guard_action(decision)
            except Exception as e:
                logger.error(f"Error in on_guard_action callback: {e}")

        return decision

