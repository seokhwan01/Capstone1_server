# -*- coding: utf-8 -*-
import pandas as pd
from math import radians, cos, sin, asin, sqrt, atan2, degrees, acos

# -----------------------------------------
# CSV 로드
# -----------------------------------------
def load_crossroad_csv(csv_path: str):
    df = pd.read_csv(csv_path)
    df.columns = df.columns.str.strip()
    return df[["itstId", "itstNm", "mapCtptIntLat", "mapCtptIntLot"]]

# -----------------------------------------
# 거리 계산 (Haversine 공식, 단위 m)
# -----------------------------------------
def haversine(lat1, lon1, lat2, lon2):
    R = 6371000  # 지구 반경 (m)
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat/2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon/2)**2
    return 2 * R * asin(sqrt(a))

# -----------------------------------------
# 각도 → 8방위 문자열 변환 (북=0° 기준)
# -----------------------------------------
def angle_to_compass(angle_deg: float) -> str:
    # 북(0), 북동(45), 동(90), 남동(135), 남(180), 남서(225), 서(270), 북서(315)
    dirs = ["북", "북동", "동", "남동", "남", "남서", "서", "북서"]
    idx = int((angle_deg % 360) / 45.0 + 0.5) % 8  # 45° 단위 라운딩
    return dirs[idx]

# -----------------------------------------
# 항법 방위각 계산 (0°=북, 시계방향 증가)
# -----------------------------------------
def bearing(lat1, lon1, lat2, lon2):
    """
    (lat1, lon1) → (lat2, lon2) 방향의 방위각을 반환
    0° = 북, 90° = 동, 180° = 남, 270° = 서
    """
    dlon = radians(lon2 - lon1)
    lat1, lat2 = radians(lat1), radians(lat2)
    y = sin(dlon) * cos(lat2)
    x = cos(lat1)*sin(lat2) - sin(lat1)*cos(lat2)*cos(dlon)
    brng = (degrees(atan2(y, x)) + 360) % 360
    return brng

# -----------------------------------------
# 회전 유형 판정 (직진 / 좌회전 / 우회전 / 유턴)
# 좌표는 (lng, lat) 순서 벡터 사용
# -----------------------------------------
def classify_turn(prev_xy, cross_xy, next_xy) -> tuple[str, float]:
    vin = (cross_xy[0] - prev_xy[0], cross_xy[1] - prev_xy[1])   # 진입 벡터
    vout = (next_xy[0] - cross_xy[0], next_xy[1] - cross_xy[1])  # 이탈 벡터

    vin_mag  = sqrt(vin[0]**2 + vin[1]**2)
    vout_mag = sqrt(vout[0]**2 + vout[1]**2)
    if vin_mag == 0 or vout_mag == 0:
        return "unknown", 0.0

    # 내적 → 두 벡터 사이 각도
    dot = vin[0]*vout[0] + vin[1]*vout[1]
    cos_theta = max(-1.0, min(1.0, dot/(vin_mag*vout_mag)))
    angle = degrees(acos(cos_theta))

    # 외적 → 좌/우 구분
    cross_val = vin[0]*vout[1] - vin[1]*vout[0]

    if angle < 30:
        turn = "직진"
    elif 60 <= angle <= 120:
        turn = "좌회전" if cross_val > 0 else "우회전"
    elif angle > 150:
        turn = "유턴"
    else:
        turn = f"기타({round(angle,1)}°)"

    return turn, angle

# -----------------------------------------
# 경로 기반 교차로 접근/이탈 방향 + 회전 타입 계산
# -----------------------------------------
def compute_crossroad_directions(route_points, crossroad_df, radius=50):
    """
    각 교차로에 대해:
      - 반경(radius) 내 진입점/이탈점(first_idx/last_idx) 찾기
      - prev(first_idx-1), next(last_idx+1) 사용해 방향 계산
      - bearing으로 진입각/이탈각 구하고 8방위 문자열 도출
    """
    results = []

    for _, row in crossroad_df.iterrows():
        cross_id = int(row["itstId"])
        cross_name = row["itstNm"]
        lat = float(row["mapCtptIntLat"])
        lon = float(row["mapCtptIntLot"])

        # 반경 내 경로 인덱스 모으기
        inside_idxs = [i for i, p in enumerate(route_points)
                       if haversine(p["lat"], p["lng"], lat, lon) <= radius]

        if not inside_idxs:
            continue

        first_idx, last_idx = inside_idxs[0], inside_idxs[-1]

        in_angle = out_angle = None
        in_dir = out_dir = None
        turn = "unknown"
        rel_angle = None

        # 진입 방향 (반경 진입 직전 → 진입점)
        if first_idx > 0:
            p_prev = route_points[first_idx-1]
            p_in   = route_points[first_idx]
            in_angle = bearing(p_prev["lat"], p_prev["lng"], p_in["lat"], p_in["lng"])
            in_dir   = angle_to_compass(in_angle)

        # 이탈 방향 (이탈점 → 반경 이탈 직후)
        if last_idx < len(route_points)-1:
            p_out  = route_points[last_idx]
            p_next = route_points[last_idx+1]
            out_angle = bearing(p_out["lat"], p_out["lng"], p_next["lat"], p_next["lng"])
            out_dir   = angle_to_compass(out_angle)

        # 회전 유형 (first_idx, last_idx 기준 벡터)
        if first_idx > 0 and last_idx < len(route_points)-1:
            prev_xy  = (route_points[first_idx-1]["lng"], route_points[first_idx-1]["lat"])
            cross_xy = (route_points[(first_idx+last_idx)//2]["lng"],  # 교차로 중심 근처
                        route_points[(first_idx+last_idx)//2]["lat"])
            next_xy  = (route_points[last_idx+1]["lng"], route_points[last_idx+1]["lat"])
            turn, rel_angle = classify_turn(prev_xy, cross_xy, next_xy)

        # 설명 문자열
        if in_dir and out_dir:
            explain = f"{in_dir}쪽에서 접근 → {out_dir}쪽으로 진행 ({turn}"
            if rel_angle is not None:
                explain += f", 상대각 {round(rel_angle,1)}°"
            explain += ")"
        elif in_dir:
            explain = f"{in_dir}쪽에서 교차로 접근 (이탈 방향 정보 부족)"
        elif out_dir:
            explain = f"교차로에서 {out_dir}쪽으로 진행 (진입 방향 정보 부족)"
        else:
            explain = "교차로 통과 정보 부족"

        results.append({
            "id": cross_id,
            "name": cross_name,
            "lat": lat,
            "lon": lon,
            "in_dir": in_dir,
            "out_dir": out_dir,
            "turn": turn,
            "in_angle": in_angle,
            "out_angle": out_angle,
            "rel_angle": rel_angle,
            "explain": explain
        })

        print(f"[DEBUG] 교차로={cross_name}, first_idx={first_idx}, last_idx={last_idx}, "
              f"in_dir={in_dir}, out_dir={out_dir}, turn={turn}, rel_angle={rel_angle}")

    return results
