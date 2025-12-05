# CUDA + Ubuntu 22.04 베이스 (T4랑 잘 맞는 12.x 대)
FROM nvidia/cuda:12.4.1-runtime-ubuntu22.04

# 파이썬 출력 버퍼링 끔
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # OMP 중복 로드 에러 방지 (너 코드에서 하던 거 글로벌로)
    KMP_DUPLICATE_LIB_OK=TRUE

WORKDIR /app

# 기본 패키지 설치
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-dev \
    build-essential \
    libgl1-mesa-glx \
    libglib2.0-0 \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# python → python3 심볼릭 링크(편의용)
RUN ln -s /usr/bin/python3 /usr/bin/python

# pip 업그레이드
RUN pip install --upgrade pip

# requirements 먼저 복사 후 설치
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 앱 소스 복사
COPY . .

# 웹(Flask) + WebSocket 포트
EXPOSE 8000 5000

# 컨테이너 시작 시 app.py 실행
CMD ["python", "app.py"]
