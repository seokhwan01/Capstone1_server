# utils/video_recorder.py
import os
import cv2
import base64
import numpy as np
import subprocess
import time
from datetime import datetime

from s3_client import s3, bucket_name
from utils.car_utils import normalize_car_no

SAVE_DIR = os.path.abspath("videos")
os.makedirs(SAVE_DIR, exist_ok=True)

# 저장용 해상도 / FPS
VIDEO_W = 640
VIDEO_H = 360
VIDEO_FPS = 15.0


class VideoRecorder:
    """
    - base64 JPG 프레임을 받아서 640x360 mp4v로 raw 녹화
    - 녹화 종료 시 ffmpeg로 H.264(mp4)로 변환
    - 변환된 파일을 S3에 업로드
    """

    def __init__(self, car_no: str, start_time: datetime):
        ts = start_time.strftime("%Y%m%d_%H%M%S")
        safe_car = normalize_car_no(car_no)

        self.base_name = f"{safe_car}_{ts}"

        # OpenCV가 쓰는 원본 파일 (mp4v)
        self.raw_path = os.path.join(SAVE_DIR, f"{self.base_name}_raw.mp4")
        # ffmpeg 변환 후 최종 파일 (H.264 mp4)
        self.final_path = os.path.join(SAVE_DIR, f"{self.base_name}.mp4")

        # S3용 파일명/키
        self.file_name = f"{self.base_name}.mp4"
        self.s3_key = f"videos/{self.file_name}"

        self.writer: cv2.VideoWriter | None = None
        self.frame_count: int = 0

        print(f"[VideoRecorder] 초기화 완료: raw={self.raw_path}, final={self.final_path} (os={os.name})")

    # ---------- 내부: VideoWriter 관리 ----------

    def _open_writer(self) -> bool:
        """
        640x360, 15fps, mp4v로 VideoWriter 오픈
        """
        if self.writer is not None:
            return True

        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        print(
            f"[VideoRecorder] VideoWriter 생성 시도: fourcc=mp4v, "
            f"size=({VIDEO_W}x{VIDEO_H}), fps={VIDEO_FPS}"
        )
        writer = cv2.VideoWriter(
            self.raw_path,
            fourcc,
            VIDEO_FPS,
            (VIDEO_W, VIDEO_H),
        )

        if writer and writer.isOpened():
            print("[VideoRecorder] ✅ VideoWriter open 성공 (mp4v)")
            self.writer = writer
            return True
        else:
            print("[VideoRecorder] ❌ VideoWriter open 실패 (mp4v)")
            self.writer = None
            return False

    def _ensure_writer(self) -> bool:
        return self._open_writer()

    # ---------- 외부: 프레임 추가 ----------

    def write_frame_b64(self, frame_b64: str):
        """
        base64 인코딩된 jpg 한 프레임을 디코드해서 영상에 추가
        """
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

            # 첫 프레임에서 writer 생성
            if not self._ensure_writer():
                return

            # 타겟 해상도(640x360)에 맞추어 리사이즈
            try:
                resized = cv2.resize(frame, (VIDEO_W, VIDEO_H))
            except Exception as e:
                print("[VideoRecorder] ⚠️ resize 실패:", e)
                resized = frame  # 실패하면 원본이라도

            if self.writer and self.writer.isOpened():
                self.writer.write(resized)
                self.frame_count += 1
                # print(
                #     f"[VideoRecorder] ✅ 프레임 기록 "
                #     f"(count={self.frame_count}, src={w}x{h}, dst={VIDEO_W}x{VIDEO_H})"
                # )

        except Exception as e:
            print("[VideoRecorder] ❌ write_frame_b64 오류:", e)

    # ---------- 내부: ffmpeg로 H.264 변환 ----------

    def _encode_h264_with_guard(self, retries: int = 3, delay: float = 1.0) -> bool:
        """
        - 프레임 수 / 파일 크기 검사
        - ffmpeg 실행 (H.264)
        - 실패해도 예외 안 터뜨리고 False 리턴
        """
        # 프레임이 너무 적으면 변환 자체를 안 함
        if self.frame_count < 5:
            print(
                f"[VideoRecorder] 프레임 {self.frame_count}개 → 너무 짧아서 "
                f"ffmpeg 변환 스킵"
            )
            return False

        if not os.path.exists(self.raw_path):
            print("[VideoRecorder] raw 파일 없음 → ffmpeg 스킵")
            return False

        file_size = os.path.getsize(self.raw_path)
        print(f"[VideoRecorder] raw 파일 크기: {file_size} bytes")
        if file_size < 50 * 1024:  # 50KB 이하이면 뭔가 이상한 파일로 간주
            print("[VideoRecorder] raw 파일 크기 너무 작음 → ffmpeg 스킵")
            return False

        # flush 조금 여유
        time.sleep(0.2)

        cmd = [
            "ffmpeg",
            "-y",
            "-fflags",
            "+genpts",
            "-i",
            self.raw_path,
            "-vcodec",
            "libx264",
            "-preset",
            "veryfast",
            "-movflags",
            "+faststart",
            "-an",  # 오디오 없음
            self.final_path,
        ]

        for attempt in range(1, retries + 1):
            print(f"[VideoRecorder] ffmpeg 변환 시도 {attempt}/{retries}")
            try:
                result = subprocess.run(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
            except Exception as e:
                print(f"❌ ffmpeg 실행 자체 실패 (시도 {attempt}): {e}")
                time.sleep(delay)
                continue

            if result.returncode == 0:
                print("[VideoRecorder] ✅ ffmpeg 변환 성공:", self.final_path)
                return True

            stderr_txt = result.stderr.decode(errors="ignore")
            print(
                f"❌ ffmpeg 변환 실패 (시도 {attempt}), returncode={result.returncode}\n"
                f"{stderr_txt[:400]}"
            )
            time.sleep(delay)

        print("[VideoRecorder] ❌ ffmpeg 변환 3회 실패 → 최종 실패 처리")
        return False

    # ---------- 외부: 마무리 + S3 업로드 ----------

    def close_and_upload(self):
        """
        - VideoWriter 닫기
        - ffmpeg로 H.264 변환
        - S3 업로드
        - 로컬 파일 정리
        """
        # 1) VideoWriter 닫기
        if self.writer is not None:
            if self.writer.isOpened():
                print(
                    f"[VideoRecorder] VideoWriter 해제 중... "
                    f"(총 프레임 수={self.frame_count})"
                )
            self.writer.release()
            print("[VideoRecorder] VideoWriter 해제 완료")
            self.writer = None
        else:
            print("[VideoRecorder] ⚠️ writer가 한 번도 생성되지 않음")

        # 2) ffmpeg 변환 시도
        encoded_ok = self._encode_h264_with_guard()

        # 3) S3 업로드 (변환 성공한 경우에만)
        if encoded_ok and os.path.exists(self.final_path):
            try:
                content_type = "video/mp4"
                s3.upload_file(
                    self.final_path,
                    bucket_name,
                    self.s3_key,
                    ExtraArgs={"ContentType": content_type},
                )
                print(
                    f"✅ 동영상 업로드 완료 → "
                    f"https://{bucket_name}.s3.us-east-1.amazonaws.com/{self.s3_key}"
                )
            except Exception as e:
                print(f"❌ 동영상 업로드 실패: {e}")
        else:
            print("[VideoRecorder] ⚠️ 인코딩 실패 또는 final 파일 없음 → 업로드 스킵")

        # 4) 로컬 파일 정리 (원하면)
        for path in (self.raw_path, self.final_path):
            try:
                if os.path.exists(path):
                    os.remove(path)
                    print(f"[VideoRecorder] 로컬 파일 삭제: {path}")
            except Exception as e:
                print(f"[VideoRecorder] 로컬 파일 삭제 실패 ({path}): {e}")
