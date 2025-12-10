# app.py
# -*- coding: utf-8 -*-
import os
import threading
from flask import Flask, redirect, url_for
from extensions import db
from config import Config

from routes import auth, dashboard, video
from sockets.ws_server import start_ws_server  # ✅ WS 서버 스타터 import
import time

APP_BOOT_ID = str(int(time.time()))  # 서버 프로세스 시작 시각


app = Flask(__name__)
app.config.from_object(Config)

db.init_app(app)


@app.context_processor
def inject_boot_id():
    return {"APP_BOOT_ID": APP_BOOT_ID}

@app.route("/")
def index():
    return redirect(url_for("auth.login"))


# 블루프린트 등록
app.register_blueprint(auth.bp)
app.register_blueprint(dashboard.bp)
app.register_blueprint(video.bp)


def run_ws():
    """
    WebSocket 서버를 Flask 앱 컨텍스트 안에서 실행
    """
    with app.app_context():
        start_ws_server()


if __name__ == "__main__":
    with app.app_context():
        db.session.expire_all()
        db.create_all()
    print("👉 DB 파일 경로:", os.path.abspath("test.db"))

    # ✅ WebSocket 서버 별도 스레드로 실행 (앱 컨텍스트 포함)
    threading.Thread(target=run_ws, daemon=True).start()

    # ✅ Flask HTTP 서버
    app.run(host="0.0.0.0", port=8000, debug=False)
