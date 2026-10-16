"""SO-100/SO-101 follower whose actions pass through the bringup-check overload guard.

Each `send_action` reads present position and load (and, every few cycles, temperature),
lets the guard adjust the goals of motors that are blocked, then sends them as usual.
If a read fails, the action goes through unguarded rather than stopping teleoperation.
"""

from __future__ import annotations

import csv
import logging
import time
from pathlib import Path

from bringup_check.guard import GuardConfig, OverloadGuard, Reading
from bringup_check.messages import t

try:  # LeRobot >= 0.5
    from lerobot.robots.so_follower import SOFollower as _Base
except ImportError:  # older layout
    from lerobot.robots.so101_follower import SO101Follower as _Base

from .config_so101_follower_guarded import SO101FollowerGuardedConfig

logger = logging.getLogger(__name__)


class SO101FollowerGuarded(_Base):
    """Keeps the parent's `name`, so the existing calibration file is reused."""

    config_class = SO101FollowerGuardedConfig

    def __init__(self, config: SO101FollowerGuardedConfig):
        super().__init__(config)
        gcfg = GuardConfig(mode=config.guard_mode)
        if config.guard_arm_load is not None:
            gcfg.arm["load_threshold"] = config.guard_arm_load
        if config.guard_gripper_load is not None:
            gcfg.grip["load_threshold"] = config.guard_gripper_load
        self.guard = OverloadGuard(gcfg)
        self._last_t: float | None = None
        self._cycle = 0
        self._temps: dict[str, float] = {}
        self._read_failed = False
        self._log_file = None
        self._log = None

    # --- bus reads ------------------------------------------------------------------
    def _read(self, data_name: str, normalize: bool = True) -> dict:
        retries = getattr(self.config, "num_read_retries", 0)
        try:
            return self.bus.sync_read(data_name, normalize=normalize, num_retry=retries)
        except TypeError:  # older bus without num_retry
            return self.bus.sync_read(data_name, normalize=normalize)

    def _readings(self) -> dict[str, Reading]:
        pos = self._read("Present_Position")
        load = self._read("Present_Load", normalize=False)  # signed, 0..1000 = 0..100 %
        if self._cycle % max(1, self.config.guard_temp_every) == 0:
            self._temps = {m: float(v) for m, v in self._read("Present_Temperature", normalize=False).items()}
        return {m: Reading(float(pos[m]), float(load[m]) / 10.0, self._temps.get(m)) for m in pos}

    # --- logging --------------------------------------------------------------------
    def _open_log(self) -> None:
        if self.config.guard_log and self._log is None:
            path = Path(self.config.guard_log).expanduser()
            path.parent.mkdir(parents=True, exist_ok=True)
            self._log_file = path.open("w", newline="", encoding="utf-8")
            self._log = csv.writer(self._log_file)
            self._log.writerow(["t", "motor", "goal", "sent", "present", "load", "temp", "released", "strain"])

    def _write_log(self, now, goals, sent, readings) -> None:
        if self._log is None:
            return
        for m, r in readings.items():
            st = self.guard.state.get(m)
            self._log.writerow([f"{now:.3f}", m, f"{goals.get(m, float('nan')):.2f}", f"{sent.get(m, float('nan')):.2f}",
                                f"{r.present:.2f}", f"{r.load:.1f}", "" if r.temp is None else f"{r.temp:.0f}",
                                int(bool(st and st.released)), f"{st.strain:.2f}" if st else "0"])
        if self._cycle % 30 == 0:
            self._log_file.flush()

    # --- robot API ------------------------------------------------------------------
    def connect(self, calibrate: bool = True) -> None:
        super().connect(calibrate)
        self._open_log()
        logger.info("Overload guard active (mode=%s)", self.config.guard_mode)

    def send_action(self, action):
        now = time.perf_counter()
        dt = 1 / 30 if self._last_t is None else now - self._last_t
        self._last_t = now
        goals = {k.removesuffix(".pos"): float(v) for k, v in action.items() if k.endswith(".pos")}
        try:
            readings = self._readings()
        except Exception as e:  # never let the guard stop teleoperation
            if not self._read_failed:
                logger.warning("Overload guard could not read the motors (%s); passing actions through unguarded.", e)
                self._read_failed = True
            return super().send_action(action)
        self._read_failed = False
        self._cycle += 1

        sent, events = self.guard.step(goals, readings, dt)
        for e in events:  # printed so they show up next to lerobot-teleoperate's live readout
            print(e.message, flush=True)
            logger.debug("guard event %s %s %s", e.kind, e.motor, e.data)
        self._write_log(now, goals, sent, readings)

        new_action = dict(action)
        new_action.update({f"{m}.pos": v for m, v in sent.items()})
        return super().send_action(new_action)

    def disconnect(self):
        try:
            s = self.guard.stats
            print(t("guard.summary", cycles=s["cycles"], warnings=s["warnings"], backoffs=s["backoffs"]), flush=True)
            if self._log_file is not None:
                self._log_file.close()
                print(t("guard.log", path=self.config.guard_log), flush=True)
                self._log_file = self._log = None
        finally:
            super().disconnect()
