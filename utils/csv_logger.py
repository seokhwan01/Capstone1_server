# csv_logger.py
import csv
import os
from datetime import datetime

from s3_client import s3, bucket_name
from utils.car_utils import normalize_car_no  # 이미 있던 함수 재사용

# 전역 상태
_csv_file = None
_csv_writer = None
_csv_file_path = None

_car_no = None
_start_time = None
_eta_time = None


def start_csv_logging(car_no: str, start_time: datetime, eta_time: datetime | None = None):
    """
    🚀 주행 시작 시 1번 호출
      - CSV 파일 생성 + 헤더 작성
      - 차량번호 / 출발시간 / ETA 기억 (summary 줄에서 사용)
    """
    global _csv_file, _csv_writer, _csv_file_path, _car_no, _start_time, _eta_time

    if not os.path.exists("logs"):
        os.makedirs("logs")

    safe_car = normalize_car_no(car_no)
    filename = f"{safe_car}_{start_time.strftime('%Y%m%d_%H%M%S')}.csv"
    _csv_file_path = os.path.join("logs", filename)

    _csv_file = open(_csv_file_path, mode="w", newline="", encoding="utf-8-sig")
    _csv_writer = csv.writer(_csv_file)

    # 🔹 헤더
    _csv_writer.writerow([
        "type",            # "point" or "summary"
        "car_no",
        "timestamp",       # point 로그용
        "lat",
        "lng",
        "speed",           # km/h
        "start_time",      # summary용
        "eta_time",        # summary용 (출발 + duration)
        "arrival_time",    # summary용 (실제 도착)
        "time_saved_sec",  # summary용 (ETA - 실제도착, 초단위, 플러스면 단축)
    ])

    _car_no = car_no
    _start_time = start_time
    _eta_time = eta_time

    print(f"📝 CSV 로깅 시작: {_csv_file_path}")


def set_eta_time(eta_time: datetime):
    """
    🔁 나중에 route에서 duration 받아서 ETA 계산한 뒤에 여기로 넣어줌
    """
    global _eta_time
    _eta_time = eta_time
    print(f"🕒 CSV ETA 설정: {_eta_time}")


def log_position(timestamp: datetime, car_no: str, lat: float, lng: float, speed: float | None):
    """
    🛰 주행 중 위치/시간/속도 로그용
      - type = "point"
    """
    global _csv_writer

    if _csv_writer is None:
        # start_csv_logging 안 된 상태면 무시
        return

    speed_str = "" if speed is None else f"{speed:.2f}"

    _csv_writer.writerow([
        "point",
        car_no,
        timestamp.strftime("%Y-%m-%d %H:%M:%S"),
        f"{lat:.8f}",
        f"{lng:.8f}",
        speed_str,
        "", "", "", ""   # start_time, eta_time, arrival_time, time_saved_sec 비워둠
    ])


def stop_csv_logging(arrival_time: datetime | None = None):
    """
    🏁 주행 종료 시 1번 호출
      - 마지막 summary 한 줄 추가
      - 파일 닫고 S3 업로드
    """
    global _csv_file, _csv_writer, _csv_file_path, _car_no, _start_time, _eta_time

    if _csv_writer is None or _csv_file is None:
        return

    start_str = _start_time.strftime("%Y-%m-%d %H:%M:%S") if _start_time else ""
    eta_str = _eta_time.strftime("%Y-%m-%d %H:%M:%S") if _eta_time else ""
    arrival_str = arrival_time.strftime("%Y-%m-%d %H:%M:%S") if arrival_time else ""

    # 🔹 단축 시간(초) 계산: ETA - 실제 도착
    #   - 일찍 도착하면 +값 (단축)
    #   - 늦게 도착하면 -값 (지연)
    time_saved_str = ""
    if _eta_time is not None and arrival_time is not None:
        delta_sec = int((_eta_time - arrival_time).total_seconds())
        time_saved_str = str(delta_sec)

    # 🔹 summary 줄
    _csv_writer.writerow([
        "summary",
        _car_no or "",
        "", "", "", "",   # timestamp, lat, lng, speed 비워둠
        start_str,
        eta_str,
        arrival_str,
        time_saved_str,
    ])

    _csv_file.close()

    try:
        s3_key = f"logs/{os.path.basename(_csv_file_path)}"
        s3.upload_file(
            _csv_file_path,
            bucket_name,
            s3_key,
            ExtraArgs={'ContentType': 'text/csv'}
        )
        print(f"✅ CSV 업로드 완료 → https://{bucket_name}.s3.us-east-1.amazonaws.com/{s3_key}")
    except Exception as e:
        print(f"❌ CSV 업로드 실패: {e}")

    _csv_file = None
    _csv_writer = None
    _csv_file_path = None
    _car_no = None
    _start_time = None
    _eta_time = None
