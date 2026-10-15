"""Bus access. `FeetechBus` talks to real servos; `FakeBus` replays a scenario."""

from __future__ import annotations

from typing import Protocol

from .messages import t
from .registers import REG


class BusError(Exception):
    pass


class Bus(Protocol):
    def ping(self, motor_id: int) -> int | None:
        """Return the model number, or None if the motor does not answer."""

    def read(self, motor_id: int, name: str) -> int | None:
        """Return a raw register value, or None if the read failed."""

    def write(self, motor_id: int, name: str, value: int) -> bool:
        """Write a raw register value. Only used by the opt-in hold test."""

    def close(self) -> None: ...


class FeetechBus:
    """Feetech STS/SMS bus through the official `scservo_sdk`.

    Install with `pip install feetech-servo-sdk` (LeRobot already depends on it).
    """

    def __init__(self, port: str, baudrate: int = 1_000_000):
        try:
            import scservo_sdk as scs  # type: ignore
        except ImportError as e:  # pragma: no cover - depends on the user's machine
            raise BusError(t("bus.err.sdk")) from e
        self._scs = scs
        self._port = scs.PortHandler(port)
        try:  # the real SDK raises serial.SerialException rather than returning False
            opened = self._port.openPort()
        except Exception as e:
            raise BusError(t("bus.err.port", port=port)) from e
        if not opened:
            raise BusError(t("bus.err.port", port=port))
        if not self._port.setBaudRate(baudrate):
            raise BusError(t("bus.err.baud", baud=baudrate))
        self._ph = scs.PacketHandler(0)  # STS/SMS series use protocol 0
        self.last_error: dict[int, int] = {}

    def _ok(self, motor_id: int, result: int, error: int) -> bool:
        if error:
            self.last_error[motor_id] = error
        return result == self._scs.COMM_SUCCESS

    def ping(self, motor_id: int) -> int | None:
        model, result, error = self._ph.ping(self._port, motor_id)
        return model if self._ok(motor_id, result, error) else None

    def read(self, motor_id: int, name: str) -> int | None:
        addr, size = REG[name]
        fn = self._ph.read1ByteTxRx if size == 1 else self._ph.read2ByteTxRx
        value, result, error = fn(self._port, motor_id, addr)
        return value if self._ok(motor_id, result, error) else None

    def write(self, motor_id: int, name: str, value: int) -> bool:
        addr, size = REG[name]
        fn = self._ph.write1ByteTxRx if size == 1 else self._ph.write2ByteTxRx
        result, error = fn(self._port, motor_id, addr, value)
        return self._ok(motor_id, result, error)

    def close(self) -> None:
        self._port.closePort()


class FakeBus:
    """In-memory bus for tests and demos.

    `motors` maps id -> {register name: raw value}; a missing id does not answer.
    `hold_effects` optionally maps id -> {register: raw value} applied while torque
    is on, to imitate how readings change under load (voltage sag, heating).
    """

    def __init__(self, motors: dict[int, dict[str, int]], hold_effects: dict | None = None,
                 model: int = 777):
        self.motors = {i: dict(r) for i, r in motors.items()}
        self.hold_effects = hold_effects or {}
        self.model = model
        self.writes: list[tuple[int, str, int]] = []

    def ping(self, motor_id: int) -> int | None:
        return self.model if motor_id in self.motors else None

    def read(self, motor_id: int, name: str) -> int | None:
        regs = self.motors.get(motor_id)
        if regs is None:
            return None
        if regs.get("Torque_Enable") and name in self.hold_effects.get(motor_id, {}):
            return self.hold_effects[motor_id][name]
        if name == "Model_Number":
            return self.model
        return regs.get(name)

    def write(self, motor_id: int, name: str, value: int) -> bool:
        if motor_id not in self.motors:
            return False
        self.writes.append((motor_id, name, value))
        self.motors[motor_id][name] = value
        return True

    def close(self) -> None:
        pass
