# bringup-check v0.1

SO-101처럼 Feetech 버스 서보(STS3215 등)로 만든 로봇을 처음 켰을 때, 모터가 원래 보내 주는 숫자(전압·온도·부하·전류·오류 상태·위치 한계)를 읽어 **셋업 문제를 사람 말로 진단**하는 도구입니다.

기존 도구는 "모터가 대답하는지"까지만 봅니다. 이 도구는 **왜** 문제가 생겼는지와 **무엇을 해 볼지**를 알려 주는 것을 목표로 합니다.

## 무엇을 잡나

| 진단 | 예시 결과 | 바탕이 된 실제 사례 |
|---|---|---|
| 체인 단선 | "3번과 4번 모터 사이 케이블을 확인하세요" | LeRobot #3131 등 |
| 한 모터만 무응답 | "ID 설정이 빠졌거나 서보 고장" | LeRobot #605, Pollen 포럼 |
| 과전압·저전압 | "모터 6개 모두 과전압: 7.6V (보호 상한 7.0V)" | Reachy Mini #589 |
| 정격과 다른 어댑터 | "7.4V용 서보에 12.1V" (`--servo-rating` 지정 시) | Arduino 포럼 |
| 체인 뒤로 전압 하락 | 헐거운 커넥터·가는 케이블 | 전원·배선 사고 다수 |
| 한 서보만 뜨거움 | "다른 서보와 바꿔 끼워 열이 따라가는지 보세요" | ROBOTIS 포럼 |
| 위치 한계·모드 설정 오류 | "한계가 2100 ≥ 2100" | Pollen 포럼 |
| 캘리브레이션 이탈·경계 | "엔코더 0/4095 경계 근처라 값이 튈 수 있음" | LeRobot #3193, #1026 |
| 쥔 채 힘쓰는 그리퍼 | "그리퍼가 멈춘 채 부하 85%" | LeRobot #2819 |
| (선택) 부하 시 전원 처짐 | "힘을 쓰자 5.0V → 4.1V" | Arduino 포럼 |

## 사용법

```bash
pip install feetech-servo-sdk   # LeRobot을 설치했다면 이미 있음

# 하드웨어 없이 재현 시나리오 보기
python -m bringup_check --demo all

# 실제 로봇 (읽기만 함, 모터를 움직이지 않음)
python -m bringup_check --port /dev/ttyACM0 --servo-rating 7.4

# LeRobot 캘리브레이션 파일까지 대조
python -m bringup_check --port /dev/ttyACM0 \
  --calib ~/.cache/huggingface/lerobot/calibration/robots/so101_follower/<내_팔_id>.json

# 선택: 모터에 힘을 넣고 현재 자세를 5초 유지하며 전원·전류 검사
python -m bringup_check --port /dev/ttyACM0 --hold-test 5 --json result.json
```

- 기본은 **읽기 전용**입니다. 모터를 움직이거나 설정을 바꾸지 않습니다.
- `--hold-test`만 모터에 힘을 넣습니다. 목표 위치를 현재 위치로 맞춘 뒤 켜기 때문에 튀지 않고, 끝나면 원래 토크 상태로 되돌립니다. 그래도 팔이 넘어지지 않게 받칠 준비를 하세요.
- `--ids`는 데이지 체인 순서대로 적습니다(기본 1~6). 단선 위치 추정이 이 순서를 씁니다.
- 결과에 `[문제]`가 있으면 종료 코드 1을 돌려줍니다.

## 한계 (v0.1)

- 진단 기준값은 공개 사고 사례를 바탕으로 정한 **출발점**이며, 실제 로그로 검증된 값이 아닙니다.
- 실물 SO-101에서는 아직 돌려 보지 않았습니다. 테스트는 공식 SDK의 호출 형식을 흉내 낸 가짜 버스와, 실제 사고를 흉내 낸 시나리오로만 했습니다.
- PWM 서보(SG90, MG996R 등)는 상태를 보내 주지 않아 진단할 수 없습니다.

## 다음 단계

1. 실물 SO-101에서 정상 상태와 일부러 만든 고장(케이블 뽑기, 약한 어댑터)으로 기준값 보정
2. 작은 왕복 동작 테스트(백래시·걸림 감지)
3. 사용자 코드 실행 전 위험 명령 검사(프리플라이트)
4. 서보 상태를 시간에 따라 추적

---

**English:** read-only setup diagnostics for Feetech serial-bus servo robots (SO-100/SO-101). Reads voltage, temperature, load, current, status flags and limits, and explains likely wiring, power, assembly and calibration causes in plain language. `--demo all` runs without hardware. Thresholds are starting points, not validated values.
