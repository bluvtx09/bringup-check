"""Known arms: which servo model each ID should report, and the supply its servos are rated for.

Values come from each kit's public documentation. They only change what counts as "unexpected"
(model check) and what the supply is compared against (voltage check); nothing is written to the servos.
"""
from __future__ import annotations

from dataclasses import dataclass

STS3215, STS3235, STS3250 = 777, 2057, 2825


@dataclass(frozen=True)
class Profile:
    name: str
    models: int | dict[int, int]     # one model number for every ID, or one per ID
    servo_rating: float | None       # volts the servos are rated for (compared with the measured supply)
    source: str = ""


PROFILES = {
    # SO-100/SO-101 (default): six STS3215. Their 7.4 V and 12 V versions report the same model
    # number, so the supply rating is left to --servo-rating.
    "so101": Profile("so101", STS3215, None),
    # Robonine SO-ARM 102 (github.com/roboninecom/SO-ARM-102, docs/specifications.md):
    # follower 12 V with STS3235 / STS3250 / STS3250 / STS3235 / ST-3215-C049 (12 V, 1:191) x2;
    # leader 5 V supply on six 7.4 V STS3215.
    "so-arm102-follower": Profile("so-arm102-follower",
                                  {1: STS3235, 2: STS3250, 3: STS3250, 4: STS3235, 5: STS3215, 6: STS3215},
                                  12.0, "roboninecom/SO-ARM-102"),
    "so-arm102-leader": Profile("so-arm102-leader", STS3215, 7.4, "roboninecom/SO-ARM-102"),
}


def expected_for(models: int | dict[int, int] | None, motor_id: int) -> int | None:
    if models is None or isinstance(models, int):
        return models
    return models.get(motor_id)
