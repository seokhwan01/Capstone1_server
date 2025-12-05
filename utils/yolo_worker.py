# utils/yolo_worker.py
import os

# ✅ OMP 에러 방지 (torch/YOLO import 전에 설정)
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

import base64
import threading
import queue
import time
from datetime import datetime

import cv2
import numpy as np
import torch
from ultralytics import YOLO
# ✅ 차량번호 안전 문자열 변환
from utils.car_utils import normalize_car_no

# ✅ S3 클라이언트
from s3_client import s3, bucket_name

# 중앙 ROI 기준 (0~1 비율)
CENTER_MIN = 0.4
CENTER_MAX = 0.6

# 로컬 저장 디렉토리 + S3 prefix
IMAGE_DIR = os.path.abspath("report_images")  # 로컬 저장 폴더
S3_IMAGE_PREFIX = "images"                    # S3 버킷 내부 prefix (폴더처럼 보임)

# ---------- 내부 상태 ----------
_frame_queue: "queue.Queue[tuple[str, str, float | None, float | None]]" = queue.Queue(maxsize=200)
_last_gps: dict[str, tuple[float | None, float | None]] = {}

# 🔥 GPU / CPU 장치 선택
if torch.cuda.is_available():
    DEVICE = "cuda:0"
else:
    DEVICE = "cpu"
print(f"[YOLO 워커] Using device: {DEVICE}")

# YOLO 모델 (장치와 함께)
_model = YOLO("best.pt")

# ---------- 추적 상태 ----------
_in_center_time: dict[tuple[str, int], float] = {}
_best_frame: dict[tuple[str, int], np.ndarray] = {}
_best_score: dict[tuple[str, int], float] = {}
_last_timestamp: dict[tuple[str, int], float] = {}
_last_bbox: dict[tuple[str, int], tuple[int, int, int, int]] = {}
_saved_ids: set[tuple[str, int]] = set()

_worker_started = False
_worker_thread: threading.Thread | None = None

# 🔥 프레임 샘플링용 (예: 3프레임 중 1개만 YOLO에 보냄)
_FRAME_SKIP = 3
_frame_counter = 0


# ---------- 외부에서 호출하는 API ----------

def update_car_gps(car_no: str, lat: float | None, lng: float | None):
    """
    WS 서버에서 current 이벤트 받을 때마다 최신 GPS 업데이트
    """
    _last_gps[car_no] = (lat, lng)


def enqueue_frame(car_no: str, frame_b64: str):
    """
    WS 서버에서 video 이벤트 받을 때 프레임 큐에 넣기
    """
    global _frame_counter
    if not frame_b64:
        return

    _frame_counter += 1

    # 🔥 프레임 샘플링: _FRAME_SKIP 값에 따라 일부 프레임만 YOLO로 보냄
    if _frame_counter % _FRAME_SKIP != 0:
        return

    # 🔥 큐가 너무 가득 차 있으면 이번 프레임은 과감하게 버림
    if _frame_queue.qsize() > 50:
        print("⚠️ [YOLO 워커] 큐 과부하 → 이번 프레임 스킵")
        return

    lat, lng = _last_gps.get(car_no, (None, None))
    try:
        _frame_queue.put_nowait((car_no, frame_b64, lat, lng))
    except queue.Full:
        print("⚠️ [YOLO 워커] frame_queue 가 가득참 → 프레임 드롭")


def start_yolo_worker():
    """
    모듈 import 시 한 번만 불러서 워커 스레드 시작
    """
    global _worker_started, _worker_thread
    if _worker_started:
        return

    _worker_thread = threading.Thread(target=_worker_loop, daemon=True)
    _worker_thread.start()
    _worker_started = True
    print("🧠 YOLO 워커 스레드 시작됨")


# ---------- 내부 워커 루프 ----------

def _worker_loop():
    os.makedirs(IMAGE_DIR, exist_ok=True)
    print("yolo 확인 (worker loop 시작)")

    while True:
        try:
            car_no, frame_b64, lat, lng = _frame_queue.get()

            # 종료 신호용 (원하면 사용)
            if frame_b64 is None:
                print("🧠 YOLO 워커 종료")
                _frame_queue.task_done()
                break

            # base64 → numpy 이미지
            try:
                # data:image/jpeg;base64,... 형식이면 앞부분 제거
                if isinstance(frame_b64, str) and frame_b64.startswith("data:"):
                    frame_b64 = frame_b64.split(",", 1)[1]

                jpg_bytes = base64.b64decode(frame_b64)
                jpg_arr = np.frombuffer(jpg_bytes, dtype=np.uint8)
                frame = cv2.imdecode(jpg_arr, cv2.IMREAD_COLOR)
                if frame is None:
                    print("[YOLO 워커] ⚠️ frame decode 실패")
                    _frame_queue.task_done()
                    continue
            except Exception as e:
                print("[YOLO 워커] ⚠️ base64 디코드 실패:", e)
                _frame_queue.task_done()
                continue

            raw_frame = frame.copy()
            h, w, _ = frame.shape
            now = time.time()

            # HUD (시간 + GPS)
            time_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if lat is not None and lng is not None:
                gps_text = f"GPS: {lat:.6f}, {lng:.6f}"
            else:
                gps_text = "GPS: -"

            cv2.putText(raw_frame, time_text, (10, 40),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.1, (255, 255, 0), 3)
            cv2.putText(raw_frame, gps_text, (10, 90),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.1, (255, 255, 0), 3)

            # ✅ 전체 프레임 YOLO 추적 (GPU / CPU 자동 선택)
            results = _model.track(
                raw_frame,
                persist=True,
                verbose=False,
                device=DEVICE,   # ← 여기서 GPU 사용
            )[0]

            if results.boxes is not None:
                for box in results.boxes:
                    if box.id is None:
                        continue

                    track_id = int(box.id[0])
                    conf = float(box.conf[0])

                    x1, y1, x2, y2 = map(int, box.xyxy[0])

                    cx = (x1 + x2) / 2
                    cx_norm = cx / w
                    is_center = CENTER_MIN < cx_norm < CENTER_MAX

                    key = (car_no, track_id)

                    if key not in _in_center_time:
                        _in_center_time[key] = 0.0
                        _best_score[key] = 0.0
                        _last_timestamp[key] = now
                        _last_bbox[key] = (x1, y1, x2, y2)

                    # 🔻 차량 한 대 감지될 때마다 로그 (필요하면 줄여도 됨)
                    print(
                        f"[YOLO 워커] 감지 car={car_no}, track_id={track_id}, "
                        f"conf={conf:.2f}, center={is_center}, bbox=({x1},{y1},{x2},{y2})"
                    )

                    if is_center:
                        _in_center_time[key] += now - _last_timestamp[key]

                        # 품질(신뢰도) 가장 좋은 프레임 저장
                        if conf > _best_score[key]:
                            _best_score[key] = conf
                            _best_frame[key] = raw_frame.copy()
                            _last_bbox[key] = (x1, y1, x2, y2)

                        # 10초 이상 중앙 유지 + 아직 저장 안 했으면 저장
                        if _in_center_time[key] >= 10 and key not in _saved_ids:
                            save_img = _best_frame[key].copy()
                            bx1, by1, bx2, by2 = _last_bbox[key]

                            cv2.rectangle(save_img, (bx1, by1), (bx2, by2),
                                        (0, 0, 255), 4)

                            # ✅ 차량번호를 S3/파일시스템용 안전 문자열로 변환
                            safe_car_no = normalize_car_no(car_no)
                            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

                            # ✅ 저장/업로드용 파일명: 예) 119da119_track1_20251205_151827.jpg
                            filename = f"{safe_car_no}_track{track_id}_{timestamp}.jpg"

                            # ✅ 로컬 저장 경로
                            save_path = os.path.join(IMAGE_DIR, filename)
                            cv2.imwrite(save_path, save_img)
                            print(f"🚨 [{car_no}] 차량 이미지 로컬 저장됨:", save_path)

                            # ✅ S3 업로드 (키에도 safe_car_no 사용)
                            try:
                                s3_key = f"{S3_IMAGE_PREFIX}/{filename}"  # images/119da119_track1_...
                                s3.upload_file(
                                    save_path,
                                    bucket_name,
                                    s3_key,
                                    ExtraArgs={"ContentType": "image/jpeg"}
                                )
                                print(f"✅ S3 업로드 완료 → https://{bucket_name}.s3.us-east-1.amazonaws.com/{s3_key}")
                            except Exception as e:
                                print(f"❌ S3 이미지 업로드 실패: {e}")

                            _saved_ids.add(key)
                            _in_center_time[key] = 0.0
                            _best_score[key] = 0.0


                    _last_timestamp[key] = now

            _frame_queue.task_done()

        except Exception as e:
            print("❌ [YOLO 워커] 처리 중 오류:", e)
