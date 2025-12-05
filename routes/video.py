# routes/video.py
import boto3
import io
import zipfile
from datetime import datetime

from flask import Blueprint, render_template, send_file, abort
from models.ambulance_log import AmbulanceLog
from botocore.exceptions import ClientError  # ✅ 에러 타입

bp = Blueprint("video", __name__)

# ✅ AWS S3 클라이언트 (진짜 서비스에선 환경변수로 빼는 거 강력 추천... 지금 이 키는 빨리 비활성화하는 게 좋음)
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

        video_logs.append({
            # 🔹 템플릿에서 ZIP 라우트 호출할 때 쓸 값들
            "car_no": log.car_no,
            "start_time": log.start_time,  # 필요하면 템플릿에서 그대로 쓰려고 같이 넘겨줌
            "start_ts": log.start_time.strftime("%Y%m%d_%H%M%S"),  # URL용

            # 🔹 화면 출력용
            "vehicle_id": log.car_no,
            "time": log.start_time.strftime("%Y-%m-%d %H:%M:%S"),

            # 🔹 S3 URL
            "url": f"{S3_BASE_URL}/{video_key}",
            "csv_url": csv_url,
        })

    return render_template("video_logs.html", video_logs=video_logs)
def _list_image_keys_for_log(car_no: str, start_time) -> list[str]:
    """
    한 출동 건에 대한 S3 이미지 key 목록
    car_no 예: '119다 119'
    start_time: datetime
    """
    # 👉 YOLO/자동신고 쪽에서 실제로 어떤 폴더에 저장하는지 여기에 맞추면 됨
    dt = start_time
    start_str = dt.strftime("%Y%m%d_%H%M%S")

    # 예: images/119다119_20251205_010203/...
    safe_car_no = car_no.replace(" ", "")
    prefix = f"images/{safe_car_no}_{start_str}/"

    keys = []
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
            if lower.endswith(".jpg") or lower.endswith(".jpeg") or lower.endswith(".png"):
                keys.append(key)

        if resp.get("IsTruncated"):
            continuation_token = resp.get("NextContinuationToken")
        else:
            break

    return keys


@bp.route("/video_logs/<string:car_no>/<string:start_ts>/images.zip")
def download_images_zip(car_no, start_ts):
    """
    car_no: URL 인코딩된 차량번호 (공백 등 포함 가능)
    start_ts: YYYYMMDD_HHMMSS
    """
    # 1) URL의 start_ts를 datetime으로 변환
    try:
        start_dt = datetime.strptime(start_ts, "%Y%m%d_%H%M%S")
    except ValueError:
        abort(400, description="잘못된 시간 형식입니다.")

    # 2) DB에서 해당 출동 로그 찾기
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

    # 5) 다운로드 파일명
    safe_car_no = log.car_no.replace(" ", "")
    download_name = f"{safe_car_no}_{start_ts}.zip"

    return send_file(
        mem_file,
        mimetype="application/zip",
        as_attachment=True,
        download_name=download_name,
    )
