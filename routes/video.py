from flask import Blueprint, render_template, Response
from models.ambulance_log import AmbulanceLog
import csv
import io

bp = Blueprint("video", __name__)

S3_BASE_URL = "https://capstone-emergency-vehicle-evasion.s3.us-east-1.amazonaws.com"

@bp.route("/video_logs")
def video_logs():
    logs = AmbulanceLog.query.order_by(AmbulanceLog.start_time.desc()).all()
    video_logs = []

    for log in logs:
        if log.video_url:  # 파일명이 있을 때만
            base_name = log.video_url.replace(".mp4", ".csv")
            video_logs.append({
                "vehicle_id": log.car_no,
                "time": log.start_time.strftime("%Y-%m-%d %H:%M:%S"),
                "url": f"{S3_BASE_URL}/videos/{log.video_url}",  # 파일명 붙이기
                "csv_url": f"{S3_BASE_URL}/logs/{base_name}" 
            })

    return render_template("video_logs.html", video_logs=video_logs)
