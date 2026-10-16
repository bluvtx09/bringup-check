from dataclasses import dataclass

from lerobot.robots.config import RobotConfig

try:  # LeRobot >= 0.5
    from lerobot.robots.so_follower import SOFollowerRobotConfig as _BaseConfig
except ImportError:  # older layout
    from lerobot.robots.so101_follower import SO101FollowerConfig as _BaseConfig


@RobotConfig.register_subclass("so101_follower_guarded")
@dataclass
class SO101FollowerGuardedConfig(_BaseConfig):
    """SO-100/SO-101 follower with the bringup-check overload guard.

    Same options as `so101_follower`, plus:
    """

    # "protect": stop a blocked motor from pushing; "warn": only print what it would do
    guard_mode: str = "protect"
    # read temperatures every N control cycles (each read costs a few ms on the bus)
    guard_temp_every: int = 10
    # optional CSV log of every cycle (goal, sent goal, position, load, temperature)
    guard_log: str | None = None
    # optional threshold overrides, % of max torque
    guard_arm_load: float | None = None
    guard_gripper_load: float | None = None
