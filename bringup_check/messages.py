"""User-facing text in Korean and English. Keys are stable; only the text changes."""

from __future__ import annotations

import locale
import os

MSG: dict[str, dict[str, str]] = {
    "ko": {
        # labels
        "label.named": "{i}번 모터({name})",
        "label.plain": "{i}번 모터",
        "level.problem": "문제", "level.warn": "주의", "level.info": "정보",
        # status bits
        "bit.voltage": "전압 이상", "bit.sensor": "센서(엔코더) 이상", "bit.temperature": "과열",
        "bit.current": "과전류", "bit.angle": "각도 범위 이상", "bit.overload": "과부하",
        "fix.voltage": "전원 전압을 확인하세요(어댑터 정격, 배선 굵기, 커넥터 접촉).",
        "fix.sensor": "엔코더 이상일 수 있습니다. 전원을 껐다 켠 뒤에도 남으면 서보 교체를 고려하세요.",
        "fix.temperature": "전원을 끄고 식히세요. 관절이 무게를 계속 버티거나 걸려 있지 않은지 보세요.",
        "fix.current": "관절이 막혀 있거나 무언가를 세게 누르고 있는지 확인하세요.",
        "fix.angle": "위치 한계 설정과 명령한 목표 위치를 확인하세요.",
        "fix.overload": "관절이 막혔거나, 그리퍼가 물체를 쥔 채 계속 닫히려 하거나, 한계 밖으로 명령했을 수 있습니다.",
        # bus
        "bus.none.title": "모터가 하나도 응답하지 않습니다",
        "bus.none.detail": "USB는 열렸지만 버스에서 어떤 모터도 대답하지 않았습니다.",
        "bus.none.fix": "① 모터 전원 어댑터가 켜져 있는지 ② 컨트롤러 보드와 첫 모터 사이 케이블 ③ 통신 속도(기본 1Mbps) ④ Waveshare 보드라면 점퍼가 B(USB) 쪽인지 확인하세요.",
        "bus.chain_break.title": "{nxt}부터 끝까지 응답이 없습니다",
        "bus.chain_break.detail": "응답한 모터: {ok} / 응답 없음: {missing}. 데이지 체인이 한 지점에서 끊긴 모양입니다.",
        "bus.chain_break.fix": "{prev}와 {nxt} 사이 케이블과 커넥터를 다시 꽂거나 교체해 보세요.",
        "bus.missing_one.title": "{m}만 응답하지 않습니다",
        "bus.missing_one.detail": "그 뒤 모터는 응답하므로 체인은 이어져 있습니다.",
        "bus.missing_one.fix": "모터 ID 설정 단계에서 이 모터에 ID가 제대로 들어갔는지 확인하세요. 맞다면 이 서보의 고장을 의심하세요.",
        "bus.model.title": "예상과 다른 서보 모델이 섞여 있습니다",
        "bus.model.fix": "부품 목록과 실제 장착된 서보를 비교하세요.",
        # voltage
        "volt.supply.title": "전원 전압 약 {v:.1f}V",
        "volt.supply.detail": "모터들이 보고한 전압의 중앙값입니다.",
        "volt.over.title": "{m} 과전압 {v:.1f}V (보호 상한 {lim:.1f}V)",
        "volt.over.merged": "모터 {n}개 모두 과전압: {v:.1f}V (보호 상한 {lim:.1f}V)",
        "volt.over.detail": "서보가 과전압 보호에 걸려 응답을 멈출 수 있습니다.",
        "volt.over.fix": "어댑터 출력 전압을 확인하고, 서보 정격에 맞는 전원을 쓰세요.",
        "volt.under.title": "{m} 저전압 {v:.1f}V (보호 하한 {lim:.1f}V)",
        "volt.under.merged": "모터 {n}개 모두 저전압: {v:.1f}V (보호 하한 {lim:.1f}V)",
        "volt.under.detail": "전원이 약하거나 커넥터 접촉이 나쁩니다.",
        "volt.under.fix": "전원 어댑터와 배선을 확인하세요.",
        "volt.merged.detail": "모터 하나가 아니라 전원 쪽 문제로 보입니다.",
        "volt.rating_high.title": "{r:g}V용 서보에 {v:.1f}V가 들어오고 있습니다",
        "volt.rating_high.detail": "정격보다 높은 전압은 서보 기판을 태울 수 있습니다.",
        "volt.rating_high.fix": "서보 정격과 어댑터를 다시 확인하세요(7.4V용과 12V용 STS3215가 따로 있습니다).",
        "volt.rating_low.title": "{r:g}V용 서보에 {v:.1f}V만 들어오고 있습니다",
        "volt.rating_low.detail": "전압이 낮으면 낼 수 있는 힘이 크게 줄어 무게를 못 버틸 수 있습니다.",
        "volt.rating_low.fix": "서보 정격에 맞는 어댑터인지 확인하세요.",
        "volt.chain_drop.title": "체인 뒤쪽으로 갈수록 전압이 {d:.1f}V 떨어집니다",
        "volt.chain_drop.fix": "중간 커넥터가 헐겁거나 케이블이 가늘 수 있습니다. 전압이 크게 떨어지는 구간의 케이블을 다시 꽂거나 교체하세요.",
        "volt.spread.title": "{m}의 전압만 낮습니다 ({v:.1f}V)",
        "volt.spread.detail": "모터 간 전압 차이 {d:.1f}V.",
        "volt.spread.fix": "이 모터로 들어가는 커넥터 접촉을 확인하세요.",
        # temperature
        "temp.near_limit.title": "{m} {t}°C — 과열 보호 직전 (한계 {lim}°C)",
        "temp.near_limit.detail": "곧 힘이 빠지거나 보호 모드로 들어갑니다.",
        "temp.hot.title": "{m} {t}°C",
        "temp.hot.detail": "오래 유지되면 수명이 줄어듭니다.",
        "temp.outlier.title": "{m}만 다른 모터보다 {d:.0f}°C 뜨겁습니다",
        "temp.outlier.detail": "이 모터 {t}°C, 나머지 중앙값 {med:.0f}°C.",
        "temp.outlier.fix": "이 관절이 무게를 혼자 버티거나 걸려 있는지 보세요. 그런 이유가 없다면 서보 개체 불량을 의심하고, 다른 관절의 서보와 바꿔 끼워 열이 따라가는지 비교하세요.",
        # status
        "status.flags.title": "{m}가 오류를 보고했습니다: {flags}",
        "status.flags.detail": "상태 레지스터 값 {raw}.",
        # limits
        "limit.wheel_mode.title": "{m}가 연속회전(바퀴) 모드로 설정돼 있습니다",
        "limit.wheel_mode.detail": "위치 한계가 0~0이면 관절이 아니라 바퀴처럼 돕니다.",
        "limit.wheel_mode.fix": "로봇팔 관절이라면 위치 한계를 되돌리세요(예: 0~4095) 또는 LeRobot 모터 설정을 다시 실행하세요.",
        "limit.invalid.title": "{m} 위치 한계가 잘못됐습니다 ({lo} ≥ {hi})",
        "limit.invalid.detail": "이 상태에서는 관절이 거의 움직이지 못하고 힘만 쓸 수 있습니다.",
        "limit.invalid.fix": "모터 설정 단계를 다시 실행해 한계를 바로잡으세요.",
        "limit.mode.title": "{m} 동작 모드가 위치 모드(0)가 아닙니다 (모드 {mode})",
        "limit.mode.detail": "관절이 위치 명령을 따르지 않을 수 있습니다.",
        "limit.mode.fix": "동작 모드를 0으로 되돌리세요.",
        "limit.narrow.title": "{m} 허용 범위가 매우 좁습니다 ({lo}~{hi})",
        "limit.narrow.detail": "명령이 이 범위 밖이면 관절이 멈춰 버립니다.",
        "limit.narrow.fix": "의도한 설정인지 확인하세요.",
        "limit.outside.title": "{m} 현재 위치({pos})가 허용 범위({lo}~{hi}) 밖입니다",
        "limit.outside.detail": "범위 안으로 들어가려 하거나, 그 방향으로 움직이지 않을 수 있습니다.",
        "limit.outside.fix": "전원을 끈 상태에서 관절을 범위 안으로 옮기고, 혼이 미끄러지지 않았는지 확인하세요.",
        # calibration
        "calib.invalid.title": "{m} 캘리브레이션 범위가 잘못됐습니다 ({lo} ≥ {hi})",
        "calib.invalid.fix": "이 관절을 다시 캘리브레이션하세요.",
        "calib.outside.title": "{m} 현재 위치({pos})가 캘리브레이션 범위({lo}~{hi}) 밖입니다",
        "calib.outside.detail": "캘리브레이션 뒤에 혼이 빠졌거나 다시 조립됐을 수 있습니다.",
        "calib.outside.fix": "혼과 나사 체결을 확인하고, 조립을 바꿨다면 다시 캘리브레이션하세요.",
        "calib.wrap.title": "{m} 현재 위치({pos})가 엔코더 경계(0/4095) 근처입니다",
        "calib.wrap.detail": "이 근처에서는 값이 반대쪽 끝으로 튀어 관절이 급하게 움직이거나 하드스톱에 부딪힐 수 있습니다.",
        "calib.wrap.fix": "관절을 범위 중간(약 2048) 근처에 두고 다시 캘리브레이션하세요.",
        # load at rest
        "load.gripper.title": "{m}가 멈춘 채 계속 힘을 쓰고 있습니다 (부하 {load:.0f}%, {cur:.0f}mA)",
        "load.gripper.detail": "물체를 쥔 채 계속 닫히려 하면 서보가 과부하로 망가질 수 있습니다(실제 사례 있음).",
        "load.gripper.fix": "그리퍼를 열거나, 쥐는 목표 위치를 물체 크기에 맞춰 덜 닫히게 하세요.",
        "load.joint.title": "{m}가 멈춘 상태에서 부하 {load:.0f}% ({cur:.0f}mA)",
        "load.joint.detail": "관절이 한계나 물체에 눌려 있거나, 무게를 혼자 오래 버티고 있습니다.",
        "load.joint.fix": "자세를 바꿔 하중을 줄이거나, 걸리는 곳이 없는지 확인하세요.",
        # hold test
        "hold.sag.title": "모터들이 힘을 쓰자 전압이 {v0:.1f}V → {v1:.1f}V로 떨어졌습니다",
        "hold.sag.detail": "전원 어댑터 용량이 부족하거나 배선이 가늘어 여러 모터를 동시에 못 버팁니다. 동작 중 끊김·떨림의 흔한 원인입니다.",
        "hold.sag.fix": "더 높은 전류(A)를 내는 어댑터를 쓰고, 브레드보드를 거치지 말고 굵은 선으로 직접 연결하세요.",
        "hold.current_outlier.title": "{m}만 자세를 유지하는 데 {c:.0f}mA를 씁니다 (다른 모터 중앙값 {med:.0f}mA)",
        "hold.current_outlier.detail": "이 관절이 무게를 혼자 버티거나, 조립이 뻑뻑하거나, 무언가에 눌려 있습니다.",
        "hold.current_outlier.fix": "전원을 끈 상태에서 그 관절을 손으로 움직여 걸리는 곳이 있는지 보세요.",
        "hold.heating.title": "{m}가 {s:.0f}초 만에 {d}°C 올랐습니다",
        "hold.heating.detail": "가만히 버티기만 해도 빠르게 뜨거워집니다.",
        "hold.status.title": "{m}가 버티는 중에 오류를 냈습니다: {flags}",
        # report
        "report.ok": "결과: 셋업에서 문제를 찾지 못했습니다.",
        "report.counts": "결과: 문제 {p}건 · 주의 {w}건",
        "report.head": "ID|관절|전압|온도|부하|전류|위치|토크|상태",
        "report.no_answer": "응답 없음",
        "report.on": "켜짐", "report.off": "꺼짐", "report.normal": "정상",
        "report.what": "무슨 일인지", "report.fix": "해볼 것", "report.note": "참고",
        # cli
        "cli.need_port": "--port 또는 --demo 중 하나가 필요합니다",
        "cli.hold_warn": "주의: 모터에 힘이 들어가 현재 자세를 유지합니다. 팔이 넘어지지 않게 받칠 준비를 하세요.",
        "cli.hold_confirm": "계속하려면 y 입력: ",
        "cli.json_saved": "JSON 저장: {path}",
        "cli.calib_found": "캘리브레이션 파일 사용: {path}",
        "cli.calib_none": "LeRobot 캘리브레이션 파일을 찾지 못해 캘리브레이션 검사는 건너뜁니다.",
        "cli.calib_many": "캘리브레이션 파일이 여러 개입니다. 가장 최근 것을 씁니다: {path} (다른 파일은 --calib 경로로 지정)",
        "cli.share": "결과를 공유해 주시면 진단 기준을 다듬는 데 큰 도움이 됩니다: {url}",
        "bus.err.sdk": "scservo_sdk가 없습니다. `pip install feetech-servo-sdk` 후 다시 실행하세요.",
        "bus.err.port": "포트 {port}를 열 수 없습니다. 포트 이름과 USB 연결을 확인하세요.",
        "bus.err.baud": "통신 속도 {baud}를 설정할 수 없습니다.",
    },
    "en": {
        "label.named": "motor {i} ({name})",
        "label.plain": "motor {i}",
        "level.problem": "PROBLEM", "level.warn": "WARNING", "level.info": "INFO",
        "bit.voltage": "voltage", "bit.sensor": "sensor (encoder)", "bit.temperature": "overheat",
        "bit.current": "over-current", "bit.angle": "angle limit", "bit.overload": "overload",
        "fix.voltage": "Check the supply voltage (adapter rating, wire gauge, connector contact).",
        "fix.sensor": "Possible encoder fault. If it survives a power cycle, consider replacing the servo.",
        "fix.temperature": "Power off and let it cool. Check that the joint isn't holding weight for long or binding.",
        "fix.current": "Check whether the joint is blocked or pressing hard on something.",
        "fix.angle": "Check the position limits and the goal positions being commanded.",
        "fix.overload": "The joint may be blocked, the gripper may keep closing on an object, or a goal outside the limits was commanded.",
        "bus.none.title": "No motor answers",
        "bus.none.detail": "The USB port opened, but no motor on the bus replied.",
        "bus.none.fix": "Check (1) the motor power adapter is on, (2) the cable from the controller board to the first motor, (3) the baud rate (default 1 Mbps), (4) on Waveshare boards, that the jumpers are on B (USB).",
        "bus.chain_break.title": "No answer from {nxt} onward",
        "bus.chain_break.detail": "Answering: {ok} / silent: {missing}. The daisy chain looks broken at one point.",
        "bus.chain_break.fix": "Reseat or replace the cable and connectors between {prev} and {nxt}.",
        "bus.missing_one.title": "Only {m} does not answer",
        "bus.missing_one.detail": "Motors after it answer, so the chain itself is intact.",
        "bus.missing_one.fix": "Check that this motor got its ID during the motor setup step. If it did, suspect this servo.",
        "bus.model.title": "Unexpected servo model on the bus",
        "bus.model.fix": "Compare the parts list with the servos actually fitted.",
        "volt.supply.title": "Supply about {v:.1f} V",
        "volt.supply.detail": "Median of the voltages the motors report.",
        "volt.over.title": "{m} over-voltage {v:.1f} V (protection limit {lim:.1f} V)",
        "volt.over.merged": "All {n} motors over-voltage: {v:.1f} V (protection limit {lim:.1f} V)",
        "volt.over.detail": "The servo may trip its over-voltage protection and stop answering.",
        "volt.over.fix": "Check the adapter's output voltage and use a supply that matches the servo rating.",
        "volt.under.title": "{m} under-voltage {v:.1f} V (protection limit {lim:.1f} V)",
        "volt.under.merged": "All {n} motors under-voltage: {v:.1f} V (protection limit {lim:.1f} V)",
        "volt.under.detail": "The supply is weak or a connector makes poor contact.",
        "volt.under.fix": "Check the power adapter and wiring.",
        "volt.merged.detail": "This points to the supply, not to one motor.",
        "volt.rating_high.title": "{r:g} V servos are getting {v:.1f} V",
        "volt.rating_high.detail": "Voltage above the rating can burn the servo board.",
        "volt.rating_high.fix": "Recheck the servo rating and the adapter (STS3215 comes in separate 7.4 V and 12 V versions).",
        "volt.rating_low.title": "{r:g} V servos are only getting {v:.1f} V",
        "volt.rating_low.detail": "At low voltage the servos lose much of their torque and may not hold the arm's weight.",
        "volt.rating_low.fix": "Check that the adapter matches the servo rating.",
        "volt.chain_drop.title": "Voltage drops {d:.1f} V along the chain",
        "volt.chain_drop.fix": "A middle connector may be loose or the cable too thin. Reseat or replace the cable where the voltage drops most.",
        "volt.spread.title": "Only {m} has low voltage ({v:.1f} V)",
        "volt.spread.detail": "Spread between motors {d:.1f} V.",
        "volt.spread.fix": "Check the connector feeding this motor.",
        "temp.near_limit.title": "{m} at {t}°C — close to thermal protection (limit {lim}°C)",
        "temp.near_limit.detail": "It will soon lose torque or enter protection.",
        "temp.hot.title": "{m} at {t}°C",
        "temp.hot.detail": "Staying this hot shortens servo life.",
        "temp.outlier.title": "Only {m} runs {d:.0f}°C hotter than the others",
        "temp.outlier.detail": "This motor {t}°C, median of the rest {med:.0f}°C.",
        "temp.outlier.fix": "Check whether this joint carries the weight alone or binds. If not, suspect the servo itself: swap it with another joint's servo and see whether the heat follows it.",
        "status.flags.title": "{m} reports: {flags}",
        "status.flags.detail": "Status register {raw}.",
        "limit.wheel_mode.title": "{m} is in continuous-rotation (wheel) mode",
        "limit.wheel_mode.detail": "Position limits of 0–0 make it spin like a wheel instead of acting as a joint.",
        "limit.wheel_mode.fix": "For an arm joint, restore the limits (e.g. 0–4095) or rerun the LeRobot motor setup.",
        "limit.invalid.title": "{m} has invalid position limits ({lo} ≥ {hi})",
        "limit.invalid.detail": "The joint can barely move and may just push.",
        "limit.invalid.fix": "Rerun the motor setup step to fix the limits.",
        "limit.mode.title": "{m} is not in position mode (mode {mode})",
        "limit.mode.detail": "The joint may ignore position commands.",
        "limit.mode.fix": "Set the operating mode back to 0.",
        "limit.narrow.title": "{m} has a very narrow allowed range ({lo}–{hi})",
        "limit.narrow.detail": "Commands outside this range will stall the joint.",
        "limit.narrow.fix": "Check that this is intended.",
        "limit.outside.title": "{m} position ({pos}) is outside its allowed range ({lo}–{hi})",
        "limit.outside.detail": "It may push toward the range or refuse to move that way.",
        "limit.outside.fix": "With power off, move the joint back into range and check that the horn hasn't slipped.",
        "calib.invalid.title": "{m} has an invalid calibration range ({lo} ≥ {hi})",
        "calib.invalid.fix": "Recalibrate this joint.",
        "calib.outside.title": "{m} position ({pos}) is outside its calibrated range ({lo}–{hi})",
        "calib.outside.detail": "The horn may have come loose or the joint was reassembled after calibration.",
        "calib.outside.fix": "Check the horn and screws, and recalibrate if you changed the assembly.",
        "calib.wrap.title": "{m} position ({pos}) is near the encoder wrap point (0/4095)",
        "calib.wrap.detail": "Readings here can jump to the other end, making the joint move suddenly or hit a hard stop.",
        "calib.wrap.fix": "Put the joint near the middle of its range (about 2048) and recalibrate.",
        "load.gripper.title": "{m} keeps pushing while stopped (load {load:.0f}%, {cur:.0f} mA)",
        "load.gripper.detail": "A gripper that keeps closing on an object can overload and break the servo (this has happened to real users).",
        "load.gripper.fix": "Open the gripper, or set the grip target to close less, matching the object's size.",
        "load.joint.title": "{m} carries {load:.0f}% load while stopped ({cur:.0f} mA)",
        "load.joint.detail": "The joint is pressed against a limit or an object, or holds weight alone for a long time.",
        "load.joint.fix": "Change the pose to reduce the load, and check for anything it is pressing on.",
        "hold.sag.title": "Supply dropped from {v0:.1f} V to {v1:.1f} V when the motors held their pose",
        "hold.sag.detail": "The adapter can't deliver enough current or the wiring is too thin for several motors at once. A common cause of dropouts and jitter during motion.",
        "hold.sag.fix": "Use an adapter with a higher current (A) rating, and wire it directly with thick wire rather than through a breadboard.",
        "hold.current_outlier.title": "Only {m} draws {c:.0f} mA to hold its pose (others' median {med:.0f} mA)",
        "hold.current_outlier.detail": "This joint holds the weight alone, is assembled too tight, or is pressing on something.",
        "hold.current_outlier.fix": "With power off, move that joint by hand and feel for binding.",
        "hold.heating.title": "{m} rose {d}°C in {s:.0f} s",
        "hold.heating.detail": "It heats quickly just holding still.",
        "hold.status.title": "{m} reported an error while holding: {flags}",
        "report.ok": "Result: no setup problems found.",
        "report.counts": "Result: {p} problem(s) · {w} warning(s)",
        "report.head": "ID|joint|volt|temp|load|current|pos|torque|status",
        "report.no_answer": "no answer",
        "report.on": "on", "report.off": "off", "report.normal": "ok",
        "report.what": "What's happening", "report.fix": "Try", "report.note": "Note",
        "cli.need_port": "Either --port or --demo is required",
        "cli.hold_warn": "Warning: the motors will be powered and hold their current pose. Be ready to support the arm.",
        "cli.hold_confirm": "Type y to continue: ",
        "cli.json_saved": "JSON saved: {path}",
        "cli.calib_found": "Using calibration file: {path}",
        "cli.calib_none": "No LeRobot calibration file found; skipping calibration checks.",
        "cli.calib_many": "Several calibration files found; using the newest: {path} (pass --calib PATH to pick another)",
        "cli.share": "Sharing your result helps tune the diagnostics: {url}",
        "bus.err.sdk": "scservo_sdk is missing. Run `pip install feetech-servo-sdk` and try again.",
        "bus.err.port": "Cannot open port {port}. Check the port name and the USB connection.",
        "bus.err.baud": "Cannot set baud rate {baud}.",
    },
}


def _default_lang() -> str:
    env = os.environ.get("BRINGUP_LANG")
    if env in MSG:
        return env
    try:
        loc = locale.getlocale()[0] or ""
    except ValueError:
        loc = ""
    return "ko" if loc.lower().startswith(("ko", "korean")) else "en"


_lang = _default_lang()


def set_lang(lang: str) -> None:
    global _lang
    if lang not in MSG:
        raise ValueError(f"unsupported language: {lang}")
    _lang = lang


def get_lang() -> str:
    return _lang


def t(key: str, **kw) -> str:
    text = MSG[_lang].get(key)
    if text is None:
        text = MSG["en"][key]
    return text.format(**kw) if kw else text


def has(key: str) -> bool:
    return key in MSG[_lang] or key in MSG["en"]
