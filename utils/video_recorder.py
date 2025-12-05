# utils/video_recorder.py
import os
import cv2
import base64
import numpy as np
from datetime import datetime

from s3_client import s3, bucket_name
from utils.car_utils import normalize_car_no

SAVE_DIR = os.path.abspath("videos")
os.makedirs(SAVE_DIR, exist_ok=True)


class VideoRecorder:
    def __init__(self, car_no: str, start_time: datetime):
        ts = start_time.strftime("%Y%m%d_%H%M%S")
        safe_car = normalize_car_no(car_no)

        # ✅ mp4 + 리눅스에서도 안전한 코덱 우선 사용
        self.ext = "mp4"
        self.preferred_fourccs = [
            ("mp4v", "MPEG-4"),  # 1순위: 대부분 환경에서 잘 됨
            ("MJPG", "MJPEG"),   # 2순위: 용량은 크지만 거의 100% 동작
        ]

        self.file_name = f"{safe_car}_{ts}.{self.ext}"
        self.file_path = os.path.join(SAVE_DIR, self.file_name)

        self.writer = None
        self.frame_count = 0
        self.frame_size = None  # (w, h)

        print(f"[VideoRecorder] 초기화 완료: {self.file_path} (os={os.name})")

    def _open_writer_with_fourcc(self, width: int, height: int):
        """여러 코덱(mp4v → MJPG) 순서대로 시도"""
        for code, name in self.preferred_fourccs:
            fourcc = cv2.VideoWriter_fourcc(*code)
            print(f"[VideoRecorder] VideoWriter 생성 시도: fourcc={code}({name}), size=({width}x{height})")

            writer = cv2.VideoWriter(
                self.file_path,
                fourcc,
                15.0,
                (width, height),
            )

            if writer and writer.isOpened():
                print(f"[VideoRecorder] ✅ VideoWriter open 성공: fourcc={code}({name})")
                self.writer = writer
                self.frame_size = (width, height)
                return True
            else:
                print(f"[VideoRecorder] ❌ fourcc={code}({name}) 로 VideoWriter open 실패")

        print("[VideoRecorder] ❌ 사용 가능한 코덱으로 VideoWriter 생성 실패")
        self.writer = None
        return False

    def _ensure_writer(self, width: int, height: int):
        if self.writer is not None:
            return True
        return self._open_writer_with_fourcc(width, height)

    def write_frame_b64(self, frame_b64: str):
        """base64 인코딩된 jpg 한 프레임을 디코드해서 영상에 추가"""
        try:
            if not frame_b64:
                print("[VideoRecorder] ⚠️ 빈 frame_b64")
                return

            # data:image/jpeg;base64,... 형식이면 앞부분 제거
            if isinstance(frame_b64, str) and frame_b64.startswith("data:"):
                try:
                    frame_b64 = frame_b64.split(",", 1)[1]
                except Exception:
                    print("[VideoRecorder] ⚠️ data URL 파싱 실패")
                    return

            jpg_bytes = base64.b64decode(frame_b64)
            jpg_arr = np.frombuffer(jpg_bytes, dtype=np.uint8)
            frame = cv2.imdecode(jpg_arr, cv2.IMREAD_COLOR)

            if frame is None:
                print("[VideoRecorder] ⚠️ frame decode 실패 (None)")
                return

            h, w = frame.shape[:2]

            # ✅ 첫 프레임에서 writer 생성
            if not self._ensure_writer(w, h):
                return

            # writer 사이즈와 다르면 맞춰주기
            if self.frame_size and (w, h) != (self.frame_size[0], self.frame_size[1]):
                frame = cv2.resize(frame, (self.frame_size[0], self.frame_size[1]))

            self.writer.write(frame)
            self.frame_count += 1
            print(f"[VideoRecorder] ✅ 프레임 기록 (count={self.frame_count}, size={w}x{h})")

        except Exception as e:
            print("[VideoRecorder] ❌ write_frame_b64 오류:", e)

    def close_and_upload(self):
        """파일 닫고 S3 업로드"""
        if self.writer is not None:
            if self.writer.isOpened():
                print(f"[VideoRecorder] VideoWriter 해제 중... (총 프레임 수={self.frame_count})")
            self.writer.release()
            print("[VideoRecorder] VideoWriter 해제 완료")
        else:
            print("[VideoRecorder] ⚠️ writer가 한 번도 생성되지 않음")

        # 파일 크기 확인
        try:
            file_size = os.path.getsize(self.file_path)
            print(f"[VideoRecorder] 로컬 파일 크기: {file_size} bytes")

            if self.frame_count == 0:
                print("[VideoRecorder] ⚠️ 경고: 기록된 프레임 수 0 → 영상이 비어 있을 수 있음")
        except Exception as e:
            print(f"[VideoRecorder] ⚠️ 파일 크기 확인 실패: {e}")
            file_size = None

        # S3 업로드
        try:
            s3_key = f"videos/{self.file_name}"
            content_type = "video/mp4"   # mp4 그대로 유지

            s3.upload_file(
                self.file_path,
                bucket_name,
                s3_key,
                ExtraArgs={'ContentType': content_type}
            )
            print(f"✅ 동영상 업로드 완료 → https://{bucket_name}.s3.us-east-1.amazonaws.com/{s3_key}")
        except Exception as e:
            print(f"❌ 동영상 업로드 실패: {e}")
