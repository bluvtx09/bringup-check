"""Feetech STS/SMS series (e.g. STS3215) register map and unit conversions.

Addresses match LeRobot's `STS_SMS_SERIES_CONTROL_TABLE`
(src/lerobot/motors/feetech/tables.py). Units follow the community
STS3215 register reference: voltage x0.1 V, current x6.5 mA,
load 0-1000 = 0-100 % with bit 10 as sign.
"""

# name: (address, size_bytes)
REG = {
    "Model_Number": (3, 2),
    "ID": (5, 1),
    "Min_Position_Limit": (9, 2),
    "Max_Position_Limit": (11, 2),
    "Max_Temperature_Limit": (13, 1),
    "Max_Voltage_Limit": (14, 1),
    "Min_Voltage_Limit": (15, 1),
    "Max_Torque_Limit": (16, 2),
    "Protection_Current": (28, 2),
    "Homing_Offset": (31, 2),
    "Operating_Mode": (33, 1),
    "Torque_Enable": (40, 1),
    "Goal_Position": (42, 2),
    "Torque_Limit": (48, 2),
    "Present_Position": (56, 2),
    "Present_Velocity": (58, 2),
    "Present_Load": (60, 2),
    "Present_Voltage": (62, 1),
    "Present_Temperature": (63, 1),
    "Status": (65, 1),
    "Moving": (66, 1),
    "Present_Current": (69, 2),
}

# Registers read for every motor in a snapshot (read-only, no motion).
SNAPSHOT_REGS = [
    "Min_Position_Limit", "Max_Position_Limit", "Max_Temperature_Limit",
    "Max_Voltage_Limit", "Min_Voltage_Limit", "Operating_Mode", "Torque_Enable",
    "Present_Position", "Present_Load", "Present_Voltage", "Present_Temperature",
    "Status", "Moving", "Present_Current",
]

MODEL_NAMES = {777: "STS3215", 2825: "STS3250", 11272: "SM8512BL"}

# Bits of the Status register (address 65), lowest bit first.
STATUS_BITS = [
    ("voltage", "전압 이상"),
    ("sensor", "센서(엔코더) 이상"),
    ("temperature", "과열"),
    ("current", "과전류"),
    ("angle", "각도 범위 이상"),
    ("overload", "과부하"),
]

POSITION_RESOLUTION = 4096


def sign_magnitude(value: int, sign_bit: int) -> int:
    """Decode a Feetech sign-magnitude value (sign stored in `sign_bit`)."""
    if value & (1 << sign_bit):
        return -(value & ((1 << sign_bit) - 1))
    return value


def volts(raw: int) -> float:
    return raw * 0.1


def milliamps(raw: int) -> float:
    return raw * 6.5


def load_percent(raw: int) -> float:
    return sign_magnitude(raw, 10) / 10.0


def decode_status(raw: int) -> list[tuple[str, str]]:
    return [bit for i, bit in enumerate(STATUS_BITS) if raw & (1 << i)]
