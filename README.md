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

## Teleop overload guard (new in v0.3)

A common way to burn a follower servo: during teleoperation the follower is blocked — the gripper is already closed on an object, or a joint is pressed into the table — while the leader keeps commanding it further (see LeRobot #2819). The servo then pushes at full load, standing still, until it overheats.

The guard is a drop-in LeRobot robot type. It watches load, position and temperature every control cycle and steps in only when a motor is **blocked** (high load, far from its goal, not moving) for more than ~1.5 s:

- **arm joints** stop pushing and back off 2° from the obstacle;
- **the gripper** keeps a *light* grip just past the contact point, so the object isn't dropped;
- a joint that is merely **carrying the arm's weight** is left alone — the guard checks this with a small 2° probe before acting, so the arm doesn't droop;
- it hands control back as soon as you move the leader away from the obstacle.

```bash
pip install "git+https://github.com/bluvtx09/bringup-check#subdirectory=plugins/lerobot_robot_so101_guarded"

# same arguments as so101_follower — only the type changes
lerobot-teleoperate \
  --robot.type=so101_follower_guarded --robot.port=/dev/ttyACM0 --robot.id=my_arm \
  --teleop.type=so101_leader --teleop.port=/dev/ttyACM1 --teleop.id=my_leader
```

Options: `--robot.guard_mode=warn` only prints what it would do; `--robot.guard_log=guard.csv` records every cycle (please attach it to a result issue — it's the data we need to tune the thresholds). It reuses your existing `so101_follower` calibration and works with `lerobot-record` too.

See what it does without a robot:

```bash
bringup-check --guard-demo
```

```
== gripper_squeeze: leader gripper closed fully on an object 35 % open (LeRobot #2819)
   guard stepped in   (first at 3.3 s)
   time at or above 25% load: without guard 8.1 s → with guard 3.4 s
== heavy_holding: shoulder at 90 % load from weight: sags 9°, looks blocked but isn't
   guard stayed out
```

Tested against LeRobot 0.6.1's own config parsing, robot factory and `send_action`, with the serial bus replaced by a simple servo model. **Not yet tested on a physical arm**; thresholds are starting points. Each cycle adds two bus reads (a few ms).

### Validate it on your arm (10 minutes, follower only — no leader needed)

```bash
so101-guard-validate --port /dev/ttyACM0 --id my_arm
```

Four guided tests: a gentle **sweep** and a stretched-out **hold** (the guard must stay out), the **wrist pushing into a book** and the **gripper squeezing a sponge** (it must step in). Positions are taught by hand with torque off, so nothing assumes which way is down; every move is speed-limited. It prints PASS/FAIL per test and saves a CSV — attaching that CSV to an issue is the single most useful thing you can do for this project. `bringup-check --analyze-log file.csv` summarises any guard log.

## Scan recorded datasets for blocked moments (new in v0.5, no robot needed)

A teleoperation dataset stores what the leader commanded (`action`) and where the follower really was (`observation.state`). When the command stays far from the position while the joint does not move, the follower was blocked: the gripper closed on an object, or a joint pressed into the table. This is the situation the overload guard targets, and `--scan-dataset` finds it using the guard's own thresholds.

```bash
pip install "bringup-check[dataset] @ git+https://github.com/bluvtx09/bringup-check"

# a Hugging Face dataset id, or a local LeRobot dataset folder; several at once is fine
bringup-check --scan-dataset lerobot/svla_so101_pickplace
bringup-check --scan-dataset ~/.cache/huggingface/lerobot/me/my_dataset --json scan.json
```

It downloads only `meta/info.json` and the parquet files (no videos), lists per joint how often and how long it was blocked, names the longest holds by episode and time so you can check them in the video, and flags joints where the leader and follower look calibrated differently.

### What 53 public SO-100/101 datasets show

Scanned 7,731 episodes (about 33 hours) from 53 public datasets on the Hub ([list](studies/public_datasets.txt)), using the guard's thresholds (gap ≥ 5 gripper / 8 arm, nearly still, ≥ 1.5 s):

- **Blocked gripper squeezes are routine.** 53 % of episodes contain at least one, in 40 of the 53 datasets. Median length 3.3 s; 16 % last over 5 s, 92 over 10 s, the longest 16.4 s.
- **Arm joints are rarely blocked.** About 3 % of episodes, concentrated in a few datasets.
- **Leader/follower calibration mismatches show up too**, on 8 joints across 6 datasets.

Two caveats. Public datasets are mostly recorded by experienced users and then curated, so they understate what happens during a first teleoperation. And datasets carry no load or temperature, so these are position-only candidates. Still, the result points the same way a kit seller's support data did: the gripper squeezing an object is where follower servos spend the most time pushing hard.

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

1. Calibrate diagnosis and guard thresholds with shared results and guard logs from real SO-101s
2. A small back-and-forth motion test for backlash and binding
3. Pre-flight check of user code before it runs on the robot (stall, limits, sudden jumps)
4. Servo health tracking over time
5. Dynamixel support (Koch arm, Reachy Mini)

## Development

```bash
pip install -e ".[test]"
pytest
```
