# bringup-check

[한국어](README.ko.md)

**Your SO-101 won't move, jitters, or a servo runs hot — and you don't know why.**
`bringup-check` reads what your Feetech servos already report (voltage, temperature, load, current, error flags, position limits) and tells you, in plain language, the most likely setup cause and what to try.

Existing tools stop at "does the motor answer?". This one tries to answer **why** it misbehaves.

```
[PROBLEM] No answer from motor 4 (wrist_flex) onward
    What's happening: Answering: [1, 2, 3] / silent: [4, 5, 6]. The daisy chain looks broken at one point.
    Try: Reseat or replace the cable and connectors between motor 3 (elbow_flex) and motor 4 (wrist_flex).
```

## Why

I went through 66 public incident reports for SO-100/101, Reachy Mini and similar hobby robots (GitHub issues and forums). **37 of the 66 were setup problems** — power, wiring, assembly, calibration — not code and not defective parts. Each check below is modelled on one of those real cases.

| Check | Example result | Based on |
|---|---|---|
| Broken daisy chain | "check the cable between motor 3 and motor 4" | LeRobot #3131 and others |
| One motor silent | "ID not set during motor setup, or a bad servo" | LeRobot #605, Pollen forum |
| Over/under-voltage | "All 6 motors over-voltage: 7.6 V (limit 7.0 V)" | Reachy Mini #589 |
| Wrong adapter for the servo | "7.4 V servos are getting 12.1 V" (with `--servo-rating`) | Arduino forum |
| Voltage falling along the chain | loose connector, thin cable | many power/wiring cases |
| One servo much hotter | "swap it with another joint's servo and see if the heat follows" | ROBOTIS forum |
| Bad position limits / mode | "invalid position limits (2100 ≥ 2100)" | Pollen forum |
| Calibration out of range / at wrap point | "near the encoder wrap point (0/4095)" | LeRobot #3193, #1026 |
| Gripper squeezing an object | "keeps pushing while stopped (load 85%)" | LeRobot #2819 |
| (optional) supply sag under load | "dropped from 5.0 V to 4.1 V" | Arduino forum |

## Install

```bash
pip install "bringup-check[hardware] @ git+https://github.com/bluvtx09/bringup-check"
```

If you already use LeRobot, the servo SDK (`feetech-servo-sdk`) is already installed.

## Use

```bash
# See every diagnosis on simulated incidents — no robot needed
bringup-check --demo all

# Your robot (read-only: nothing moves, nothing is written)
bringup-check --port /dev/ttyACM0 --servo-rating 7.4 --calib auto

# Optional: power the motors and hold the current pose for 5 s to test the supply
bringup-check --port /dev/ttyACM0 --hold-test 5
```

- `--calib auto` finds your LeRobot calibration file and picks the one that matches the arm on the bus.
- `--ids` lists motor IDs **in daisy-chain order** (default `1,2,3,4,5,6`); the broken-cable guess uses this order.
- `--lang en|ko` sets the language (default: your system language).
- `--hold-test` is the only option that powers the motors. It sets each goal to the current position first, so nothing jumps, and restores the original torque state afterwards. Still, be ready to support the arm.
- Exit code is 1 when a `[PROBLEM]` is found.

## Please share your result

The thresholds are starting points taken from public reports, **not yet validated on real robots**. If you run it on your robot — healthy or not — please open an issue with:

```bash
bringup-check --port /dev/ttyACM0 --calib auto --share
```

and tell us whether the diagnosis matched the real cause. → [Share a result](https://github.com/bluvtx09/bringup-check/issues/new?template=diagnosis-report.md)

The `--share` block contains only motor readings and finding codes — no file paths or port names.

## Limits

- Supports Feetech STS/SMS serial-bus servos (STS3215 in SO-100/101). Dynamixel and other buses are not supported yet.
- PWM hobby servos (SG90, MG996R…) report nothing back, so they can't be diagnosed.
- Not yet run on a physical SO-101. Tests use a stand-in bus with the official SDK's call shapes, and scenarios that imitate real incidents.

## Roadmap

1. Calibrate thresholds with shared results and a real SO-101 (healthy vs. deliberately faulted)
2. A small back-and-forth motion test for backlash and binding
3. Pre-flight check of user code before it runs on the robot (stall, limits, sudden jumps)
4. Servo health tracking over time
5. Dynamixel support (Koch arm, Reachy Mini)

## Development

```bash
pip install -e ".[test]"
pytest
```
