"""Overload guard for teleoperation.

Background: the most common after-sales failure a kit seller reported is a follower
servo burning out the first time a new user teleoperates. A typical way it happens
(LeRobot #2819): the follower is blocked — the gripper is already closed on an
object, or a joint is pressed into the table — while the leader keeps commanding a
position past the obstacle. The servo then pushes at high load, standing still,
until it overheats.

The guard watches each follower motor every control cycle and tells two situations
apart:

* **blocked**: high load, a large gap between goal and present position, and no
  motion. The motor is pushing against something it cannot move. If this lasts,
  the guard replaces that motor's goal so it stops pushing hard. The motor keeps
  holding, it never goes limp, and it resumes following once the operator's command
  no longer pushes into the obstacle.
    - arm joints back off a little, away from the obstacle (e.g. the table);
    - the gripper keeps a *light* grip just past the contact point, so a grasped
      object is held with less force instead of being dropped.
* **holding**: high load but goal ≈ present — the joint is carrying the arm's
  weight. Releasing it would drop the arm, so the guard never backs off here; it
  only warns when the motor gets hot.

This module is hardware-free: feed it readings, get adjusted goals and events back.
Thresholds are starting points, not values validated on real arms.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .messages import t


ARM_DEFAULTS = {
    "load_threshold": 45.0,   # % of max torque that counts as pushing hard
    "gap_threshold": 8.0,     # goal-present gap (deg) that counts as "wants to go further";
                              # larger than gravity sag of a loaded joint at LeRobot's low P gain
    "action": "backoff",
    "hold_offset": 2.0,       # back off this far from the obstacle (deg)
}
GRIPPER_DEFAULTS = {
    "load_threshold": 25.0,   # LeRobot caps the gripper at 50 % torque, so its readings run lower
    "gap_threshold": 5.0,     # % of gripper range
    "action": "light_hold",
    "hold_offset": 1.5,       # keep pressing this far past the contact point (% of range)
}


@dataclass
class GuardConfig:
    mode: str = "protect"            # "protect" changes goals; "warn" only reports
    still_speed: float = 6.0          # below this |velocity| (units/s) the motor counts as not moving
    warn_after: float = 0.8           # seconds of full-load pushing before a warning
    act_after: float = 1.5            # seconds of full-load pushing before acting
    decay: float = 1.0                # strain shed per second when not blocked
    resume_margin: float = 1.0        # resume once the command is this far back from the hold point
    probe_s: float = 0.3              # how long to probe whether a stalled joint is blocked or holding weight
    probe_step: float = 2.0           # how far to ease the command back while probing (deg)
    temp_warn: float = 55.0           # °C
    temp_stop: float = 63.0           # °C, stop pushing (firmware limit is usually 70)
    temp_resume: float = 55.0         # °C, allow pushing again
    gripper: tuple[str, ...] = ("gripper",)
    arm: dict = field(default_factory=lambda: dict(ARM_DEFAULTS))
    grip: dict = field(default_factory=lambda: dict(GRIPPER_DEFAULTS))
    overrides: dict = field(default_factory=dict)   # {motor: {param: value}}

    def __post_init__(self):
        if self.mode not in ("protect", "warn"):
            raise ValueError("mode must be 'protect' or 'warn'")
        if not (0 < self.warn_after <= self.act_after):
            raise ValueError("need 0 < warn_after <= act_after")
        if self.temp_resume > self.temp_stop:
            raise ValueError("temp_resume must not exceed temp_stop")

    def param(self, motor: str, name: str):
        if name in self.overrides.get(motor, {}):
            return self.overrides[motor][name]
        return (self.grip if motor in self.gripper else self.arm)[name]


@dataclass
class Reading:
    present: float                    # present position (same units as goal)
    load: float                       # signed load in % of max torque
    temp: float | None = None         # °C, may be read less often


@dataclass
class Event:
    level: str                        # "warn" | "act" | "info"
    kind: str                         # strain | backoff | light_hold | heavy_hold | resume | hot | too_hot | cooled
    motor: str
    message: str
    data: dict = field(default_factory=dict)


@dataclass
class _MotorState:
    strain: float = 0.0
    last_present: float | None = None
    warned: bool = False
    released: bool = False
    push_dir: int = 0                 # +1 / -1: direction the motor was pushing when released
    hold_at: float | None = None      # goal used while released
    released_for: float = 0.0         # seconds since the guard stepped in
    confirmed: bool = False           # backoff joints: probe showed a real obstacle
    probe_from: float | None = None   # position when the probe started
    holding_lock: bool = False        # load turned out to be the arm's weight: don't intervene
    hot: bool = False
    too_hot: bool = False
    temp: float | None = None


class OverloadGuard:
    def __init__(self, config: GuardConfig | None = None):
        self.cfg = config or GuardConfig()
        self.state: dict[str, _MotorState] = {}
        self.stats = {"cycles": 0, "backoffs": 0, "warnings": 0}

    def _st(self, motor: str) -> _MotorState:
        return self.state.setdefault(motor, _MotorState())

    def step(self, goals: dict[str, float], readings: dict[str, Reading], dt: float) -> tuple[dict[str, float], list[Event]]:
        """Return the goals to send and the events of this cycle.

        `goals` are what the operator (leader) asks for; motors missing from
        `readings` pass through unchanged.
        """
        cfg = self.cfg
        dt = max(dt, 1e-3)
        out = dict(goals)
        events: list[Event] = []
        self.stats["cycles"] += 1

        for motor, goal in goals.items():
            r = readings.get(motor)
            if r is None:
                continue
            st = self._st(motor)
            speed = 0.0 if st.last_present is None else abs(r.present - st.last_present) / dt
            st.last_present = r.present
            if r.temp is not None:
                st.temp = r.temp

            gap = goal - r.present
            load = abs(r.load)
            load_thr = cfg.param(motor, "load_threshold")
            if st.holding_lock and load < 0.5 * load_thr:
                st.holding_lock = False
            blocked = (not st.holding_lock and load >= load_thr
                       and abs(gap) >= cfg.param(motor, "gap_threshold") and speed <= cfg.still_speed)

            # temperature (independent of blocked/holding)
            if st.temp is not None:
                if not st.hot and st.temp >= cfg.temp_warn:
                    st.hot = True
                    events.append(Event("warn", "hot", motor, t("guard.hot", m=motor, t=st.temp), {"temp": st.temp}))
                elif st.hot and st.temp < cfg.temp_warn - 3:
                    st.hot = False
                if not st.too_hot and st.temp >= cfg.temp_stop:
                    st.too_hot = True
                    events.append(Event("act" if cfg.mode == "protect" else "warn", "too_hot", motor,
                                        t("guard.too_hot", m=motor, t=st.temp), {"temp": st.temp}))
                elif st.too_hot and st.temp <= cfg.temp_resume:
                    st.too_hot = False
                    events.append(Event("info", "cooled", motor, t("guard.cooled", m=motor, t=st.temp), {"temp": st.temp}))

            # strain: seconds of pushing, weighted by how hard
            if blocked:
                st.strain += dt * min(load, 100.0) / 100.0
            else:
                st.strain = max(0.0, st.strain - cfg.decay * dt)

            if not st.warned and st.strain >= cfg.warn_after:
                st.warned = True
                self.stats["warnings"] += 1
                events.append(Event("warn", "strain", motor, t("guard.strain", m=motor, load=load), {"load": load}))
            elif st.warned and st.strain == 0.0 and not st.released:
                st.warned = False

            should_release = blocked and (st.strain >= cfg.act_after or st.too_hot)
            if not st.released and should_release:
                st.released = True
                st.released_for = 0.0
                st.push_dir = 1 if gap > 0 else -1
                action = cfg.param(motor, "action")
                offset = cfg.param(motor, "hold_offset")
                # light_hold: stay just past contact. backoff: first probe (see below), then step away.
                st.hold_at = r.present + offset * st.push_dir if action == "light_hold" else None
                st.confirmed = action != "backoff"
                st.probe_from = r.present
                if cfg.mode != "protect":
                    events.append(Event("warn", action, motor, t("guard.backoff_warn", m=motor, load=load, s=st.strain),
                                        {"load": load, "strain": round(st.strain, 2)}))
                elif action == "light_hold":
                    self.stats["backoffs"] += 1
                    events.append(Event("act", action, motor, t("guard.light_hold", m=motor, load=load, s=st.strain),
                                        {"load": load, "strain": round(st.strain, 2), "hold_at": st.hold_at}))

            if st.released and cfg.mode == "protect" and not st.confirmed:
                # Probe: ease the command back by a small step. Against a real obstacle the joint
                # stays put (it is pinned to the obstacle). A joint carrying the arm's weight
                # follows the eased command and moves; then the load is the weight, so undo and
                # leave this joint alone instead of letting the arm droop.
                st.released_for += dt
                step = cfg.probe_step
                if abs(r.present - st.probe_from) >= 0.5 * step:
                    st.released = False
                    st.holding_lock = True
                    st.warned = False
                    st.strain = 0.0
                    st.hold_at = None
                    events.append(Event("warn", "heavy_hold", motor, t("guard.heavy_hold", m=motor, load=load),
                                        {"load": load}))
                    continue
                if st.released_for < cfg.probe_s:
                    out[motor] = goal - st.push_dir * step
                    continue
                st.confirmed = True
                st.hold_at = r.present - cfg.param(motor, "hold_offset") * st.push_dir
                self.stats["backoffs"] += 1
                events.append(Event("act", "backoff", motor, t("guard.backoff", m=motor, load=load, s=st.strain),
                                    {"load": load, "strain": round(st.strain, 2), "hold_at": st.hold_at}))

            if st.released:
                # how far the operator's command still reaches past the hold point, in the push direction
                still_pushing = st.hold_at is not None and (goal - st.hold_at) * st.push_dir > -cfg.resume_margin
                if st.hold_at is None:  # warn mode, backoff joint: nothing to hold, just track resume
                    still_pushing = (goal - r.present) * st.push_dir > -cfg.resume_margin
                if still_pushing or (st.too_hot and (goal - r.present) * st.push_dir > 0):
                    if cfg.mode == "protect" and st.hold_at is not None:
                        out[motor] = st.hold_at
                else:
                    st.released = False
                    st.released_for = 0.0
                    st.confirmed = False
                    st.probe_from = None
                    st.warned = False
                    st.strain = 0.0
                    st.hold_at = None
                    events.append(Event("info", "resume", motor, t("guard.resume", m=motor)))
        return out, events
