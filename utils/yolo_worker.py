# utils/yolo_worker.py
import os

# ✅ OMP 에러 방지
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

from utils.car_utils import normalize_car_no
from s3_client import s3, bucket_name

# 중앙 ROI 기준 (0~1 비율)
CENTER_MIN = 0.4
CENTER_MAX = 0.6

# 최소 confidence 기준
CONF_THRESHOLD = 0.6

# 👉 디버그용 폴더 (실제 JPG는 저장 안 하지만, 필요하면 찍어볼 때 사용)
IMAGE_DIR = os.path.abspath("report_images")
S3_IMAGE_PREFIX = "images"

_frame_queue: "queue.Queue[tuple[str, str, float | None, float | None]]" = queue.Queue(maxsize=200)
_last_gps: dict[str, tuple[float | None, float | None]] = {}

# 🔹 각 차량별 출동 시작 시각 (문자열 "YYYYMMDD_HHMM%S")
_car_start_ts: dict[str, str] = {}

# 🔥 GPU / CPU 선택
if torch.cuda.is_available():
    DEVICE = "cuda:0"
else:
    DEVICE = "cpu"
print(f"[YOLO 워커] Using device: {DEVICE}")

# YOLO 모델
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

# 프레임 샘플링 (1이면 스킵 없음)
_FRAME_SKIP = 1
_frame_counter = 0

# 🔽 저장할 이미지 해상도 (너무 크지 않게)
SAVE_W = 640
SAVE_H = 640
JPEG_QUALITY = 90


# ---------- 공용 함수: S3 업로드 리트라이 ----------

def _upload_bytes_to_s3_with_retry(
    data: bytes,
    s3_key: str,
    content_type: str,
    retries: int = 3,
    delay: float = 1.0,
) -> bool:
    """
    S3 업로드가 가끔 실패해도 워커가 죽지 않도록,
    정해진 횟수만큼 재시도하고 실패하면 False 리턴.
    """
    for attempt in range(1, retries + 1):
        try:
            s3.put_object(
                Bucket=bucket_name,
                Key=s3_key,
                Body=data,
                ContentType=content_type,
            )
            print(
                f"✅ 자동 신고 찰영 "
                f"https://{bucket_name}.s3.us-east-1.amazonaws.com/{s3_key}"
            )
            return True
        except Exception as e:
            print(f"❌ S3 업로드 실패({attempt}/{retries}): {e}")
            time.sleep(delay)
    return False


# ---------- 외부 API ----------

def update_car_gps(car_no: str, lat: float | None, lng: float | None):
    """
    WS 서버에서 current 이벤트 받을 때마다 최신 GPS 업데이트
    """
    _last_gps[car_no] = (lat, lng)


def set_run_start_time(car_no: str, start_time: datetime):
    """
    출동이 시작될 때(ambulance_start) 호출해서
    해당 차량의 출동 시작 시각을 기록.
    - VideoRecorder에서 쓰는 start_time과 같은 값을 넣어주면
      비디오/CSV/이미지 파일 네이밍을 맞출 수 있음.
    """
    ts = start_time.strftime("%Y%m%d_%H%M%S")
    _car_start_ts[car_no] = ts
    print(f"[YOLO 워커] set_run_start_time car={car_no}, ts={ts}")

    # 🔄 이 차량에 대한 이전 추적 상태 초기화
    keys_to_clear = [k for k in _in_center_time.keys() if k[0] == car_no]

    for k in keys_to_clear:
        _in_center_time.pop(k, None)
        _best_frame.pop(k, None)
        _best_score.pop(k, None)
        _last_timestamp.pop(k, None)
        _last_bbox.pop(k, None)
        _saved_ids.discard(k)


def enqueue_frame(car_no: str, frame_b64: str):
    """
    WS 서버에서 video 이벤트 받을 때 프레임 큐에 넣기
    """
    global _frame_counter
    if not frame_b64:
        return

    _frame_counter += 1

    # 프레임 샘플링 (_FRAME_SKIP=1이면 스킵 없음)
    if _frame_counter % _FRAME_SKIP != 0:
        return

    # 큐 과부하 방지
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


# ---------- 내부 유틸: IoU 기반 기존 트랙 매칭 ----------

def _find_match_key_for_new_box(
    car_no: str,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    iou_thresh: float = 0.5,
) -> tuple[str, int] | None:
    """
    새 박스가 들어왔을 때, 같은 차량(car_no)에 대해
    이전 bbox들과 IoU를 비교해서 충분히 겹치는 트랙이 있으면 그 key를 반환.
    없으면 None.
    """
    best_key = None
    best_iou = 0.0

    for (c, tid), (ox1, oy1, ox2, oy2) in _last_bbox.items():
        if c != car_no:
            continue

        # 교집합
        inter_x1 = max(x1, ox1)
        inter_y1 = max(y1, oy1)
        inter_x2 = min(x2, ox2)
        inter_y2 = min(y2, oy2)

        inter_w = max(0, inter_x2 - inter_x1)
        inter_h = max(0, inter_y2 - inter_y1)
        inter_area = inter_w * inter_h
        if inter_area <= 0:
            continue

        # 합집합
        area_new = (x2 - x1) * (y2 - y1)
        area_old = (ox2 - ox1) * (oy2 - oy1)
        union_area = area_new + area_old - inter_area
        if union_area <= 0:
            continue

        iou = inter_area / union_area
        if iou > iou_thresh and iou > best_iou:
            best_iou = iou
            best_key = (c, tid)

    return best_key


# ---------- 내부 워커 루프 ----------

def _worker_loop():
    os.makedirs(IMAGE_DIR, exist_ok=True)
    print("yolo 확인 (worker loop 시작)")

    while True:
        try:
            car_no, frame_b64, lat, lng = _frame_queue.get()

            # 종료 신호
            if frame_b64 is None:
                print("🧠 YOLO 워커 종료")
                _frame_queue.task_done()
                break

            # base64 → numpy
            try:
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

            cv2.putText(
                raw_frame,
                time_text,
                (10, 40),
                cv2.FONT_HERSHEY_SIMPLEX,
                1.1,
                (255, 255, 0),
                3,
            )
            cv2.putText(
                raw_frame,
                gps_text,
                (10, 90),
                cv2.FONT_HERSHEY_SIMPLEX,
                1.1,
                (255, 255, 0),
                3,
            )

            # YOLO 추적
            results = _model.track(
                raw_frame,
                persist=True,
                verbose=False,
                device=DEVICE,
            )[0]

            if results.boxes is not None:
                for box in results.boxes:
                    if box.id is None:
                        continue

                    track_id = int(box.id[0])
                    conf = float(box.conf[0])

                    # 🔽 confidence 기준 이하 박스 무시
                    if conf < CONF_THRESHOLD:
                        continue

                    x1, y1, x2, y2 = map(int, box.xyxy[0])

                    cx = (x1 + x2) / 2
                    cx_norm = cx / w
                    is_center = CENTER_MIN < cx_norm < CENTER_MAX

                    key = (car_no, track_id)

                    # 🔗 새 트랙인데, 이전 박스와 많이 겹치면 상태 이어받기
                    if key not in _in_center_time:
                        match_key = _find_match_key_for_new_box(
                            car_no, x1, y1, x2, y2, iou_thresh=0.5
                        )

                        if match_key is not None:
                            # 이전 키의 상태를 새 키로 옮기기
                            _in_center_time[key] = _in_center_time.pop(match_key, 0.0)
                            _best_score[key] = _best_score.pop(match_key, 0.0)
                            if match_key in _best_frame:
                                _best_frame[key] = _best_frame.pop(match_key)
                            _last_timestamp[key] = _last_timestamp.pop(match_key, now)
                            _last_bbox[key] = (x1, y1, x2, y2)

                            if match_key in _saved_ids:
                                _saved_ids.add(key)
                                _saved_ids.discard(match_key)

                            print(
                                f"[YOLO 워커] 🔗 ID 머지: {match_key} → {key} (IoU 기반)"
                            )
                        else:
                            # 완전히 새로운 트랙
                            _in_center_time[key] = 0.0
                            _best_score[key] = 0.0
                            _last_timestamp[key] = now
                            _last_bbox[key] = (x1, y1, x2, y2)
                    else:
                        # 기존 트랙이면 bbox/타임스탬프 업데이트
                        _last_bbox[key] = (x1, y1, x2, y2)

                    print(
                        f"[YOLO 워커] 감지 car={car_no}, track_id={track_id}, "
                        f"conf={conf:.2f}, center={is_center}, "
                        f"bbox=({x1},{y1},{x2},{y2})"
                    )

                    if is_center:
                        _in_center_time[key] += now - _last_timestamp.get(key, now)

                        # 품질(신뢰도) 가장 좋은 프레임 저장
                        if conf > _best_score.get(key, 0.0):
                            _best_score[key] = conf
                            _best_frame[key] = raw_frame.copy()
                            _last_bbox[key] = (x1, y1, x2, y2)

                        # 10초 이상 중앙 유지 + 아직 저장 안 했으면
                        if _in_center_time[key] >= 10 and key not in _saved_ids:
                            if key not in _best_frame:
                                print(f"[YOLO 워커] ⚠️ best_frame 없음 → 저장 스킵 (key={key})")
                            else:
                                save_img = _best_frame[key].copy()
                                bx1, by1, bx2, by2 = _last_bbox[key]

                                cv2.rectangle(
                                    save_img,
                                    (bx1, by1),
                                    (bx2, by2),
                                    (0, 0, 255),
                                    4,
                                )

                                # 해상도 줄이기
                                try:
                                    save_img_resized = cv2.resize(
                                        save_img, (SAVE_W, SAVE_H)
                                    )
                                except Exception as e:
                                    print("[YOLO 워커] ⚠️ resize 실패:", e)
                                    save_img_resized = save_img

                                safe_car_no = normalize_car_no(car_no)

                                # 🔹 출동 시작 시각 기준으로 파일명 구성
                                start_ts = _car_start_ts.get(car_no)
                                if start_ts is None:
                                    # 혹시 set_run_start_time을 안 부른 경우 fallback
                                    start_ts = datetime.now().strftime("%Y%m%d_%H%M%S")

                                # ➜ images/{safe_car}_track{ID}_{start_ts}.jpg
                                filename = f"{safe_car_no}_track{track_id}_{start_ts}.jpg"
                                s3_key = f"{S3_IMAGE_PREFIX}/{filename}"

                                # 메모리에서 바로 JPEG 인코딩 → S3 업로드
                                ok, buf = cv2.imencode(
                                    ".jpg",
                                    save_img_resized,
                                    [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY],
                                )
                                if not ok:
                                    print("❌ [YOLO 워커] JPEG 인코딩 실패 → 업로드 스킵")
                                else:
                                    img_bytes = buf.tobytes()
                                    _upload_bytes_to_s3_with_retry(
                                        img_bytes,
                                        s3_key,
                                        "image/jpeg",
                                    )

                                _saved_ids.add(key)
                                _in_center_time[key] = 0.0
                                _best_score[key] = 0.0

                    _last_timestamp[key] = now

            _frame_queue.task_done()

        except Exception as e:
            print("❌ [YOLO 워커] 처리 중 오류:", e)
