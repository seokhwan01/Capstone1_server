# sockets/ws_server.py
# -*- coding: utf-8 -*-
import json
import asyncio
from datetime import datetime, timedelta
from sockets.ambulance_state import update_ambulance_position
import websockets
from extensions import db
from models.ambulance_log import AmbulanceLog
from utils.car_utils import normalize_car_no
from utils.crossroad_utils import (
    load_crossroad_csv,
    compute_crossroad_directions,
    haversine,
)

from sockets.route_matcher import (
    normal_car_tracks,
    ambulance_routes,
    check_same_road_and_direction,
    get_any_ambulance_route,
)

from utils.video_recorder import VideoRecorder
from utils.csv_logger import start_csv_logging, log_position, stop_csv_logging, set_eta_time
# 🔽 YOLO 워커 관련 추가
from utils.yolo_worker import start_yolo_worker, enqueue_frame, update_car_gps,set_run_start_time

# 차량별 비디오 레코더
recorders: dict[str, VideoRecorder] = {}

# 차량별 예상 교차로 (경로 기반 분석 결과)
expected_crossroads: dict[str, list[dict]] = {}

# 교차로 정보
crossroad_df = load_crossroad_csv("static/crossroad_map/CrossroadMap.csv")

# WebSocket 서버
clients: set[websockets.WebSocketServerProtocol] = set()

# ✅ 각 WebSocket 연결이 어떤 차량인지 매핑
ws_car_map: dict[websockets.WebSocketServerProtocol, str] = {}




async def broadcast_dict(data: dict):
    if not clients:
        return
    msg = json.dumps(data, ensure_ascii=False)
    await asyncio.gather(
        *[c.send(msg) for c in list(clients)],
        return_exceptions=True,
    )


async def ws_handler(websocket):
    print("🔌 WebSocket Client Connected")
    clients.add(websocket)

    try:
        async for msg in websocket:
            try:
                data = json.loads(msg)
            except Exception as e:
                print("⚠️ JSON 파싱 실패:", e, msg[:120])
                continue

            t = data.get("type")

            if t != "video":
                print("📥 WS 메시지 수신:", msg[:120])
                print(f"📡 [WS 수신] type={t}, keys={list(data.keys())}")

            # --------------------------------------------------
            # 1) 출발 이벤트
            # --------------------------------------------------
            if t == "start":
                try:
                    car_no = data.get("car")
                    start_time_str = data.get("start_time") or data.get("time")

                    start_time = datetime.strptime(
                        start_time_str, "%Y-%m-%d %H:%M:%S"
                    )

                    normalized_car_no = normalize_car_no(car_no)
                    timestamp = start_time.strftime("%Y%m%d_%H%M%S")
                    file_name = f"{normalized_car_no}_{timestamp}.mp4"

                    log = AmbulanceLog(
                        car_no=car_no,
                        start_time=start_time,
                        video_url=file_name,
                    )
                    db.session.merge(log)
                    db.session.commit()

                    print(f"✅ DB INSERT: {car_no}, 출발={start_time}, 파일명={file_name}")

                    # VideoRecorder
                    try:
                        rec = VideoRecorder(car_no, start_time)
                        recorders[car_no] = rec
                        print(f"🎥 VideoRecorder 생성 완료: {car_no}")
                    except Exception as e:
                        print("❌ VideoRecorder 생성 실패:", e)

                    # ✅ 이 WebSocket이 어떤 차량인지 매핑
                    if car_no:
                        ws_car_map[websocket] = car_no
                        print(f"🔗 WebSocket ↔ 차량 매핑: {websocket} -> {car_no}")

                    # CSV 로깅 시작
                    try:
                        start_csv_logging(car_no, start_time, eta_time=None)
                    except Exception as e:
                        print("⚠️ CSV start 실패:", e)

                    # 🔥 YOLO 워커 출동 시작 시간 설정 (여기가 핵심)
                    try:
                        set_run_start_time(car_no, start_time)
                    except Exception as e:
                        print("⚠️ YOLO set_run_start_time 실패:", e)


                    out = {
                        "event": "ambulance_start",
                        **data,
                    }
                    await broadcast_dict(out)

                except Exception as e:
                    print("❌ start 처리 오류:", e)

            # --------------------------------------------------
            # 2) 도착 이벤트
            # --------------------------------------------------
            elif t == "arrival":
                try:
                    car_no = data.get("car")
                    start_time_str = data.get("start_time")  # or latest 찾기
                    arrival_time_str = data.get("arrival_time") or data.get("time")

                    arrival_time = datetime.strptime(
                        arrival_time_str, "%Y-%m-%d %H:%M:%S"
                    )

                    start_time = None

                    if start_time_str:
                        start_time = datetime.strptime(
                            start_time_str, "%Y-%m-%d %H:%M:%S"
                        )
                        log = db.session.get(AmbulanceLog, (car_no, start_time))
                    else:
                        log = (
                            db.session.query(AmbulanceLog)
                            .filter(AmbulanceLog.car_no == car_no)
                            .order_by(AmbulanceLog.start_time.desc())
                            .first()
                        )
                        if log:
                            start_time = log.start_time

                    if log:
                        log.arrival_time = arrival_time
                        db.session.commit()
                        print(f"✅ DB UPDATE(도착): {car_no}, 도착={arrival_time}")
                    else:
                        print("⚠️ 도착 로그 업데이트 대상 없음:", car_no)

                    # VideoRecorder 종료
                    rec = recorders.pop(car_no, None)
                    if rec:
                        print(f"🎥 {car_no} VideoRecorder 종료 및 업로드")
                        rec.close_and_upload()
                    else:
                        print(f"⚠️ {car_no} 에 대한 VideoRecorder 없음")

                    # CSV summary + 업로드
                    try:
                        stop_csv_logging(arrival_time)
                    except Exception as e:
                        print("⚠️ CSV stop 실패:", e)

                    if car_no:
                        expected_crossroads.pop(car_no, None)

                    out = {
                        "event": "ambulance_arrival",
                        "car": car_no,
                        "start_time": start_time.strftime("%Y-%m-%d %H:%M:%S") if start_time else None,
                        "arrival_time": arrival_time.strftime("%Y-%m-%d %H:%M:%S"),
                    }
                    await broadcast_dict(out)

                except Exception as e:
                    print("❌ arrival 처리 오류:", e)

            # --------------------------------------------------
            # 3) 경로 이벤트
            # --------------------------------------------------
            elif t == "route":
                try:
                    route_points = data.get("route_points") or data.get("path") or []
                    norm_points = []
                    for p in route_points:
                        if isinstance(p, (list, tuple)) and len(p) >= 2:
                            norm_points.append({"lat": float(p[0]), "lng": float(p[1])})
                        elif isinstance(p, dict):
                            norm_points.append(
                                {
                                    "lat": float(p.get("lat")),
                                    "lng": float(p.get("lng")),
                                }
                            )

                    data["route_points"] = norm_points

                    print("🚑 경로 좌표 샘플:", norm_points[:2])

                    car_no = data.get("car")

                    # ✅ 여기서 구급차 polyline 저장
                    if car_no:
                        ambulance_routes[car_no] = norm_points
                        print(f"🗺 구급차 경로 저장 완료: car={car_no}, points={len(norm_points)}")

                    # duration(초) → ETA 계산
                    duration_sec = data.get("duration")

                    if car_no and duration_sec is not None:
                        try:
                            log = (
                                db.session.query(AmbulanceLog)
                                .filter(AmbulanceLog.car_no == car_no)
                                .order_by(AmbulanceLog.start_time.desc())
                                .first()
                            )

                            if log and log.start_time:
                                eta_time = log.start_time + timedelta(seconds=int(duration_sec))
                                set_eta_time(eta_time)
                                print(
                                    f"🕒 ETA 설정 완료: car={car_no}, "
                                    f"start={log.start_time}, duration={duration_sec}s, eta={eta_time}"
                                )
                            else:
                                print("⚠️ ETA 계산용 start_time 로그를 찾지 못함:", car_no)
                        except Exception as e:
                            print("⚠️ ETA 계산/저장 실패:", e)

                    if car_no:
                        crossroads = compute_crossroad_directions(
                            norm_points,
                            crossroad_df,
                            radius=50,
                        )

                        for c in crossroads:
                            c["status"] = "pending"

                        expected_crossroads[car_no] = crossroads

                        print("🚦 예상 교차로 및 접근 방향:")
                        for c in crossroads:
                            print(
                                f"  - {c['name']}: {c['explain']} "
                                f"(진입={c['in_dir']} → 이탈={c['out_dir']}, turn={c['turn']})"
                            )
                    else:
                        print("⚠️ route 데이터에 car 필드가 없음:", data)

                    ack = {
                        "type": "success",
                        "status": "success",
                    }
                    await websocket.send(json.dumps(ack, ensure_ascii=False))

                    out = {
                        "event": "ambulance_route",
                        **data,
                    }
                    await broadcast_dict(out)

                    if car_no:
                        await broadcast_dict(
                            {
                                "event": "ambulance_expected_crossroads",
                                "car": car_no,
                                "crossroads": expected_crossroads[car_no],
                            }
                        )

                except Exception as e:
                    print("⚠️ route 처리 오류:", e)
                    err_msg = {
                        "type": "error",
                        "error": str(e),
                    }
                    await websocket.send(json.dumps(err_msg, ensure_ascii=False))

            # --------------------------------------------------
            # 4) 앰뷸런스 현재 위치
            # --------------------------------------------------
            elif t == "current":
                print("🚑 current 수신:", data)
                current = data.get("current", {})
                lat_raw = current.get("lat")
                lon_raw = current.get("lng")
                speed = data.get("speed")
                car_no = data.get("car")

                # ✅ 숫자 변환
                lat = float(lat_raw) if lat_raw is not None else None
                lon = float(lon_raw) if lon_raw is not None else None

                # ✅ YOLO 워커에 GPS 업데이트
                if car_no:
                    update_car_gps(car_no, lat, lon)

                # ✅ HTTP 폴링용 최신 위치 저장
                if car_no and lat is not None and lon is not None:
                    update_ambulance_position(
                        car_no,
                        lat,
                        lon,
                        float(speed) if speed is not None else None,
                    )

                # CSV 로그 기록
                if car_no and lat is not None and lon is not None:
                    try:
                        ts = datetime.now()
                        log_position(
                            ts,
                            car_no,
                            lat,
                            lon,
                            float(speed) if speed is not None else None,
                        )
                    except Exception as e:
                        print("⚠️ CSV 위치 로그 실패:", e)

                if lat is not None and lon is not None and car_no:
                    try:
                        lat_f, lon_f = lat, lon

                        crossroads = expected_crossroads.get(car_no, [])
                        if not crossroads:
                            print(
                                f"🚦 차량 {car_no}에 대해 저장된 expected_crossroads 없음"
                            )
                        else:
                            for c in crossroads:
                                d = haversine(lat_f, lon_f, c["lat"], c["lon"])

                                if c["status"] == "pending" and d <= 300:
                                    print(
                                        f"⚠️ 교차로 접근 알림: {c['name']} "
                                        f"(진입={c['in_dir']} → 이탈={c['out_dir']}, "
                                        f"turn={c['turn']}, 거리={d:.1f}m)"
                                    )
                                    c["status"] = "approaching"

                                    await broadcast_dict(
                                        {
                                            "event": "ambulance_crossroad_approach",
                                            "car": car_no,
                                            "crossroad_id": c["id"],
                                            "crossroad_name": c["name"],
                                            "turn": c.get("turn"),
                                            "in_dir": c.get("in_dir"),
                                            "out_dir": c.get("out_dir"),
                                            "explain": c.get("explain"),
                                            "distance": round(d, 1),
                                            "timestamp": datetime.now().isoformat(),
                                        }
                                    )

                                elif c["status"] == "approaching" and d <= 50:
                                    print(f"🚦 교차로 도착: {c['name']} (거리={d:.1f}m)")
                                    c["status"] = "arrived"

                                    await broadcast_dict(
                                        {
                                            "event": "ambulance_crossroad_arrived",
                                            "car": car_no,
                                            "crossroad_id": c["id"],
                                            "crossroad_name": c["name"],
                                            "distance": round(d, 1),
                                            "timestamp": datetime.now().isoformat(),
                                        }
                                    )

                                elif c["status"] == "arrived" and d > 50:
                                    print(f"✅ 교차로 통과 완료: {c['name']}")
                                    c["status"] = "passed"

                                    await broadcast_dict(
                                        {
                                            "event": "ambulance_crossroad_passed",
                                            "car": car_no,
                                            "crossroad_id": c["id"],
                                            "crossroad_name": c["name"],
                                            "distance": round(d, 1),
                                            "timestamp": datetime.now().isoformat(),
                                        }
                                    )

                    except Exception as e:
                        print("⚠️ 교차로/거리 계산 오류:", e)
                else:
                    print("⚠️ current 좌표 또는 car 번호 없음:", data)

                out = {
                    "event": "ambulance_current",
                    **data,
                }
                await broadcast_dict(out)

            # --------------------------------------------------
            # 5) 일반 차량 현재 위치
            # --------------------------------------------------
            elif t == "normal_current":
                print("🚗 일반 차량 현재 위치 수신:", data)

                car_id = data.get("car")
                current = data.get("current", {})
                lat_raw = current.get("lat")
                lon_raw = current.get("lng")

                same_road = False
                same_dir = False
                ref_amb_car = None

                try:
                    # ✅ 방어 로직 추가
                    if car_id is None or lat_raw is None or lon_raw is None:
                        print("⚠️ normal_current 좌표/차량 정보 부족:", data)
                    else:
                        lat = float(lat_raw)
                        lon = float(lon_raw)

                        # 1) 차량별 좌표 저장
                        normal_car_tracks[car_id].append({"lat": lat, "lng": lon})
                        track_points = list(normal_car_tracks[car_id])

                        # 2) 구급차 경로 하나 가져오기
                        ref_amb_car, amb_route = get_any_ambulance_route()

                        if amb_route:
                            same_road, same_dir = check_same_road_and_direction(
                                amb_route,
                                track_points,
                            )
                            print(
                                f"🔍 일반차 {car_id} vs 구급차 {ref_amb_car}: "
                                f"same_road={same_road}, same_dir={same_dir}"
                            )
                        else:
                            print("⚠️ 비교할 구급차 경로 없음")

                except Exception as e:
                    print("⚠️ normal_current 처리 오류:", e)

                out = {
                    "event": "normalcar_current",
                    "same_road": same_road,
                    "same_dir": same_dir,
                    "same_road_and_dir": same_road and same_dir,
                    "ref_ambulance_car": ref_amb_car,
                    **data,
                }
                await broadcast_dict(out)


            # --------------------------------------------------
            # 6) 영상 프레임
            # --------------------------------------------------
            elif t == "video":
                car_no = data.get("car")
                frame_b64 = data.get("frame")

                # ✅ 메시지에 car가 없으면 WebSocket 매핑에서 가져오기
                if not car_no:
                    car_no = ws_car_map.get(websocket)

                if not car_no:
                    # print("[video] ⚠ car_no를 찾을 수 없음 (메시지에도 없고 ws_car_map에도 없음)")
                    continue

                if frame_b64:
                    # 1) 대시보드에 브로드캐스트
                    out = {
                        "event": "video",
                        "car": car_no,
                        "frame": frame_b64,
                    }
                    await broadcast_dict(out)

                    # 2) ✅ YOLO 워커 큐에 프레임 전달 (백그라운드에서 분석/이미지 저장)
                    enqueue_frame(car_no, frame_b64)

                    # 3) 기존 VideoRecorder 녹화 유지
                    rec = recorders.get(car_no)
                    if rec:
                        rec.write_frame_b64(frame_b64)
                    else:
                        pass

            else:
                print(f"❓ 알 수 없는 type 수신: {t}, data={data}")

    except websockets.exceptions.ConnectionClosed:
        print("❌ WebSocket Client Disconnected")
    finally:
        clients.remove(websocket)
        ws_car_map.pop(websocket, None)  # ✅ 연결 끊길 때 매핑 제거


async def ws_main():
    print("🌐 WebSocket Server running ws://0.0.0.0:5000")
    async with websockets.serve(ws_handler, "0.0.0.0", 5000, ping_interval=None):
        await asyncio.Future()  # run forever


def start_ws_server():
    print("🔧 WebSocket Server starting...")
    #YOLO 워커 스레드 시작
    start_yolo_worker()
    asyncio.run(ws_main())
