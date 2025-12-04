# imbeded_web/models/ambulance_log.py
from datetime import datetime
from extensions import db   # Flask SQLAlchemy 객체

class AmbulanceLog(db.Model):
    __tablename__ = "ambulance_logs"

    car_no = db.Column(db.String(20), primary_key=True)
    start_time = db.Column(db.DateTime, primary_key=True)
    arrival_time = db.Column(db.DateTime, nullable=True)
    video_url = db.Column(db.Text, nullable=True)

