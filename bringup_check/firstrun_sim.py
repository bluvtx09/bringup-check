"""A moving stand-in bus for the first-run safety mode, so it can be tested without an arm.

Each motor is a saturating P controller on a damped joint, in raw encoder steps. The
torque cap follows the Torque_Limit register, gravity loads a joint, a wall stands for
anything in the way, and a weak supply sags with total load. Like guard_sim, the numbers
are illustrative, not fitted to a real STS3215.
"""

from __future__ import annotations

from dataclasses import dataclass

from .scenarios import _calib, healthy_motors


@dataclass
class Joint:
    x: float
    gravity: float = 0.0          # % load needed to hold the joint; pulls toward -x
    wall: float | None = None     # an obstacle position (steps)
    wall_side: int = 0            # +1: cannot pass the wall going up; -1: going down
    kp: float = 2.0               # % load per step of error
    damping: float = 0.15         # % load per (step/s)
    error_bits_when_on: int = 0   # Status bits this servo raises once powered (a faulty servo)
    load: float = 0.0


class SimBus:
    def __init__(self, joints: dict[int, Joint], regs: dict[int, dict[str, int]],
                 supply_v: float = 5.0, sag_per_pct: float = 0.0, model: int = 777):
        self.j = joints
        self.regs = {i: dict(r) for i, r in regs.items()}
        for i, r in self.regs.items():
            r.setdefault("Torque_Limit", 1000)
            r["Goal_Position"] = int(joints[i].x)
        self.supply_v, self.sag_per_pct, self.model = supply_v, sag_per_pct, model
        self.writes: list[tuple[int, str, int]] = []
        self.t = 0.0

    # Bus protocol
    def ping(self, i):
        return self.model if i in self.regs else None

    def read(self, i, name):
        if i not in self.regs:
            return None
        j, r = self.j[i], self.regs[i]
        if name == "Present_Position":
            return int(round(j.x))
        if name == "Present_Load":
            raw = int(round(abs(j.load) * 10))
            return raw | (1 << 10) if j.load < 0 else raw
        if name == "Present_Voltage":
            total = sum(abs(m.load) for m in self.j.values())
            return int(round((self.supply_v - self.sag_per_pct * total) * 10))
        if name == "Status":
            return j.error_bits_when_on if r.get("Torque_Enable") else 0
        if name == "Model_Number":
            return self.model
        return r.get(name)

    def write(self, i, name, value):
        if i not in self.regs:
            return False
        self.writes.append((i, name, value))
        self.regs[i][name] = value
        return True

    def close(self):
        pass

    # physics, advanced by the first-run loop's sleep
    def advance(self, dt: float) -> None:
        self.t += dt
        for i, j in self.j.items():
            r = self.regs[i]
            if not r.get("Torque_Enable"):
                j.load = 0.0
                continue
            cap = r.get("Torque_Limit", 1000) / 10.0
            torque = max(-cap, min(cap, j.kp * (r["Goal_Position"] - j.x)))
            v = (torque - j.gravity) / j.damping
            nx = j.x + v * dt
            if j.wall is not None and j.wall_side and (nx - j.wall) * j.wall_side > 0:
                nx = j.wall
            j.x = nx
            j.load = torque


def _joints(**overrides) -> dict[int, Joint]:
    pos = {i: r["Present_Position"] for i, r in healthy_motors().items()}
    js = {i: Joint(x=float(p)) for i, p in pos.items()}
    # shoulder_lift rests on its stop and needs ~25 % to lift the arm off it
    js[2] = Joint(x=float(pos[2]), gravity=25.0, wall=pos[2] - 2, wall_side=-1)
    for i, kw in overrides.items():
        for k, v in kw.items():
            setattr(js[int(i)], k, v)
    return js


def scenarios() -> dict[str, dict]:
    """name -> {bus factory, calib, what should happen}."""
    calib = _calib()
    sc = {}
    sc["healthy"] = {"title": "Healthy arm: the shoulder needs more than 20 % to lift the arm's weight",
                     "make": lambda: SimBus(_joints(), healthy_motors()), "calib": calib,
                     "expect": {"completed": True, "codes": ["fr.weight"]}}
    # elbow (pos 2950, range 900-3100) moves toward the middle, i.e. down; a wall 5 steps away
    sc["stuck_elbow"] = {"title": "Elbow can't move: a screw too tight or a cable caught in the joint",
                         "make": lambda: SimBus(_joints(**{"3": {"wall": 2945.0, "wall_side": -1}}), healthy_motors()),
                         "calib": calib, "expect": {"completed": False, "codes": ["fr.blocked"]}}
    sc["weak_supply"] = {"title": "Supply sags as soon as a joint works hard",
                         "make": lambda: SimBus(_joints(), healthy_motors(), sag_per_pct=0.05),
                         "calib": calib, "expect": {"completed": False, "codes": ["fr.safety_sag"]}}
    sc["faulty_servo"] = {"title": "Wrist servo raises an overload flag as soon as it is powered",
                          "make": lambda: SimBus(_joints(**{"4": {"error_bits_when_on": 1 << 5}}), healthy_motors()),
                          "calib": calib, "expect": {"completed": False, "codes": ["fr.safety_status"]}}

    def missing():
        regs = healthy_motors()
        del regs[5]
        js = _joints()
        del js[5]
        return SimBus(js, regs)
    sc["chain_break"] = {"title": "Motor 5 does not answer", "make": missing, "calib": calib,
                         "expect": {"completed": False, "codes": ["bus.missing_one"]}}
    sc["no_calibration"] = {"title": "No calibration file yet",
                            "make": lambda: SimBus(_joints(), healthy_motors()), "calib": None,
                            "expect": {"completed": False, "codes": ["fr.no_calib"]}}
    return sc
