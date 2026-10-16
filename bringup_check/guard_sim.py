"""A deliberately simple servo model to exercise the overload guard without hardware.

Each motor is a saturating P controller that drives a damped joint. It can push against
an obstacle (a wall in one direction) and carry a constant gravity load. The numbers are
illustrative, chosen so the qualitative situations look right; they are not a model
fitted to a real STS3215.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .guard import GuardConfig, OverloadGuard, Reading


@dataclass
class SimMotor:
    x: float                      # position (deg, or % for the gripper)
    kp: float = 10.0              # % load per unit of error
    max_load: float = 100.0       # torque cap in % (LeRobot sets the gripper to 50 %)
    gravity: float = 0.0          # constant load the joint must hold, signed (+ pulls toward +x)
    wall: float | None = None     # obstacle position
    wall_side: int = 0            # +1: cannot pass wall going up; -1: going down
    damping: float = 0.5          # % load per (unit/s)
    max_speed: float = 270.0
    temp: float = 30.0
    load: float = 0.0

    def step(self, goal: float, dt: float) -> None:
        torque = max(-self.max_load, min(self.max_load, self.kp * (goal - self.x)))
        net = torque - self.gravity
        v = max(-self.max_speed, min(self.max_speed, net / self.damping))
        nx = self.x + v * dt
        if self.wall is not None and self.wall_side and (nx - self.wall) * self.wall_side > 0:
            nx = self.wall
        self.x = nx
        self.load = torque
        # crude thermal model: heating ~ load^2, cooling toward 30 °C
        self.temp += dt * (0.004 * (abs(torque) ** 2) / 100.0 - 0.02 * (self.temp - 30.0))


@dataclass
class Scenario:
    name: str
    motor: str
    sim: SimMotor
    leader: callable              # t -> goal
    seconds: float
    expect_action: bool
    note: str = ""


def scenarios() -> list[Scenario]:
    def ramp_hold(start, end, t_ramp, t_hold_end=None, back=None):
        def f(t):
            if t < t_ramp:
                return start + (end - start) * t / t_ramp
            if t_hold_end is not None and back is not None and t >= t_hold_end:
                return back
            return end
        return f

    return [
        Scenario("gripper_squeeze", "gripper",
                 SimMotor(x=60.0, kp=10.0, max_load=50.0, wall=35.0, wall_side=-1),
                 ramp_hold(60.0, 0.0, 0.5, 8.0, 50.0), 10.0, True,
                 "leader gripper closed fully on an object 35 % open (LeRobot #2819)"),
        Scenario("elbow_into_table", "elbow_flex",
                 SimMotor(x=0.0, wall=-20.0, wall_side=-1),
                 ramp_hold(0.0, -45.0, 1.0, 7.0, -10.0), 9.0, True,
                 "leader pushed the arm 25° into the table"),
        Scenario("normal_motion", "elbow_flex",
                 SimMotor(x=0.0),
                 lambda t: 60.0 * math.sin(2 * math.pi * 0.5 * t), 10.0, False,
                 "fast back-and-forth teleop, nothing in the way"),
        Scenario("holding_weight", "shoulder_lift",
                 SimMotor(x=30.0, gravity=-45.0),
                 lambda t: 30.0, 10.0, False,
                 "shoulder holding the arm out, 45 % load from gravity"),
        Scenario("heavy_holding", "shoulder_lift",
                 SimMotor(x=30.0, gravity=-90.0),
                 lambda t: 30.0, 10.0, False,
                 "shoulder at 90 % load from weight: sags 9°, looks blocked but isn't"),
        Scenario("brief_touch", "elbow_flex",
                 SimMotor(x=0.0, wall=-20.0, wall_side=-1),
                 ramp_hold(0.0, -30.0, 0.3, 1.0, 0.0), 4.0, False,
                 "taps the table for under a second, then lifts"),
        Scenario("normal_grasp_light", "gripper",
                 SimMotor(x=60.0, kp=10.0, max_load=50.0, wall=35.0, wall_side=-1),
                 ramp_hold(60.0, 33.0, 0.5), 8.0, False,
                 "closes just onto the object without squeezing past it"),
    ]


@dataclass
class SimResult:
    name: str
    acted: bool
    first_action_s: float | None
    resumed: bool
    seconds_over_threshold: float
    peak_temp: float
    events: list = field(default_factory=list)


def run(sc: Scenario, guard: bool = True, config: GuardConfig | None = None, fps: float = 30.0) -> SimResult:
    import copy
    sim = copy.deepcopy(sc.sim)
    g = OverloadGuard(config or GuardConfig()) if guard else None
    dt = 1.0 / fps
    thr = (config or GuardConfig()).param(sc.motor, "load_threshold")
    over, peak, events, first = 0.0, sim.temp, [], None
    n = int(sc.seconds * fps)
    for k in range(n):
        t = k * dt
        goal = sc.leader(t)
        if g:
            sent, evs = g.step({sc.motor: goal}, {sc.motor: Reading(sim.x, sim.load, sim.temp)}, dt)
            goal = sent[sc.motor]
            for e in evs:
                events.append((round(t, 2), e))
                if e.level == "act" and first is None:
                    first = t
        sim.step(goal, dt)
        if abs(sim.load) >= thr:
            over += dt
        peak = max(peak, sim.temp)
    resumed = any(e.kind == "resume" for _, e in events)
    return SimResult(sc.name, first is not None, first, resumed, over, peak, events)
