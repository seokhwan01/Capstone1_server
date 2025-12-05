# routes/video.py
import boto3
import io
import zipfile
from datetime import datetime

from flask import Blueprint, render_template, send_file, abort
from models.ambulance_log import AmbulanceLog
from botocore.exceptions import ClientError

# ✅ 차량번호 → 안전 문자열 변환 (한글 → 영문화, 특수문자 → _)
from utils.car_utils import normalize_car_no

bp = Blueprint("video", __name__)

# ✅ AWS S3 클라이언트 (실서비스에선 env로 빼기)
s3 = boto3.client(
    "s3",
    aws_access_key_id="AKIAQOAKFOWUA3FXVWU5",
    aws_secret_access_key="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",
    region_name="us-east-1"
)

bucket_name = "capstone-emergency-vehicle-evasion"
S3_BASE_URL = "https://capstone-emergency-vehicle-evasion.s3.us-east-1.amazonaws.com"


@bp.route("/video_logs")
def video_logs():
    logs = AmbulanceLog.query.order_by(AmbulanceLog.start_time.desc()).all()
    video_logs = []

    for log in logs:
        if not log.video_url:
            continue

        video_key = f"videos/{log.video_url}"
        csv_key = f"logs/{log.video_url.replace('.mp4', '.csv')}"

        # ✅ CSV 존재 여부 확인
        try:
            s3.head_object(Bucket=bucket_name, Key=csv_key)
            csv_url = f"{S3_BASE_URL}/{csv_key}"
        except ClientError:
            csv_url = None

        start_ts = log.start_time.strftime("%Y%m%d_%H%M%S")

        # ✅ 이미지 존재 여부 확인
        try:
            image_keys = _list_image_keys_for_log(log.car_no, log.start_time)
            has_images = len(image_keys) > 0
        except ClientError:
            has_images = False

        video_logs.append({
            "car_no": log.car_no,
            "start_time": log.start_time,
            "start_ts": start_ts,

            "vehicle_id": log.car_no,
            "time": log.start_time.strftime("%Y-%m-%d %H:%M:%S"),

            "url": f"{S3_BASE_URL}/{video_key}",
            "csv_url": csv_url,

            # 🔹 템플릿에서 쓸 플래그
            "has_images": has_images,
        })

    return render_template("video_logs.html", video_logs=video_logs)


def _list_image_keys_for_log(car_no: str, start_time) -> list[str]:
    """
    한 출동 건에 대한 S3 이미지 key 목록
    - yolo_worker 저장 패턴:
      images/{normalize_car_no(car_no)}_track{N}_YYYYMMDD_HHMMSS.jpg
    """
    dt = start_time
    start_str = dt.strftime("%Y%m%d_%H%M%S")

    # ✅ 차량번호를 S3 경로용으로 normalize
    safe_car_no = normalize_car_no(car_no)

    # 예: images/119da119_track1_20251205_151827.jpg
    # → 앞부분 공통 prefix: images/119da119_track
    prefix = f"images/{safe_car_no}_track"

    keys: list[str] = []
    continuation_token = None

    while True:
        params = {
            "Bucket": bucket_name,
            "Prefix": prefix,
        }
        if continuation_token:
            params["ContinuationToken"] = continuation_token

        resp = s3.list_objects_v2(**params)
        contents = resp.get("Contents", [])

        for obj in contents:
            key = obj["Key"]
            lower = key.lower()

            # 끝이 _YYYYMMDD_HHMMSS.(jpg|jpeg|png) 인 애들만 (해당 출동)
            if lower.endswith(f"_{start_str}.jpg") or \
               lower.endswith(f"_{start_str}.jpeg") or \
               lower.endswith(f"_{start_str}.png"):
                keys.append(key)

        if resp.get("IsTruncated"):
            continuation_token = resp.get("NextContinuationToken")
        else:
            break

    return keys


@bp.route("/video_logs/<string:car_no>/<string:start_ts>/images.zip")
def download_images_zip(car_no, start_ts):
    """
    car_no : URL에서 넘어온 차량번호 (원본, DB에 있는 값)
    start_ts : 'YYYYMMDD_HHMMSS'
    """
    # 1) URL의 start_ts를 datetime으로 변환
    try:
        start_dt = datetime.strptime(start_ts, "%Y%m%d_%H%M%S")
    except ValueError:
        abort(400, description="잘못된 시간 형식입니다.")

    # 2) DB에서 해당 출동 로그 찾기 (PK = car_no + start_time)
    log = AmbulanceLog.query.filter_by(car_no=car_no, start_time=start_dt).first()
    if not log:
        abort(404, description="해당 출동 로그를 찾을 수 없습니다.")

    # 3) S3에서 이미지 목록 조회
    keys = _list_image_keys_for_log(log.car_no, log.start_time)
    if not keys:
        abort(404, description="해당 출동 건에 대한 이미지가 없습니다.")

    # 4) ZIP 메모리 생성
    mem_file = io.BytesIO()
    with zipfile.ZipFile(mem_file, "w", zipfile.ZIP_DEFLATED) as zf:
        for key in keys:
            obj = s3.get_object(Bucket=bucket_name, Key=key)
            data = obj["Body"].read()
            filename = key.split("/")[-1]
            zf.writestr(filename, data)

    mem_file.seek(0)

    # 5) 다운로드 파일명 (보기 좋게 normalize 써도 되고, 원본 써도 됨)
    safe_car_no = normalize_car_no(log.car_no)
    download_name = f"{safe_car_no}_{start_ts}.zip"

    return send_file(
        mem_file,
        mimetype="application/zip",
        as_attachment=True,
        download_name=download_name,
    )
