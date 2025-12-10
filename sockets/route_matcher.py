# sockets/route_matcher.py
# -*- coding: utf-8 -*-

import math
from collections import defaultdict, deque
from typing import List, Dict, Tuple
from utils.crossroad_utils import haversine


# ================================================================
# 차량 좌표 저장 공간
# ================================================================
normal_car_tracks: Dict[str, deque] = defaultdict(lambda: deque(maxlen=10))

# 구급차 경로 저장 공간 (car_no → polyline)
ambulance_routes: Dict[str, List[dict]] = {}

# ================================================================
# polyline 비교 유틸 함수
# ================================================================

def point_to_polyline_distance(lat: float, lon: float, poly: List[dict]):
    """
    점(lat, lon)과 polyline 각 점까지의 거리 중 최소값과 인덱스 반환
    """
    min_d = float("inf")
    min_idx = 0
    for i, p in enumerate(poly):
        d = haversine(lat, lon, p["lat"], p["lng"])
        if d < min_d:
            min_d = d
            min_idx = i
    return min_d, min_idx


def is_on_same_road(
    ambulance_route: List[dict],
    car_points: List[dict],
    dist_threshold: float = 25.0,
    ratio_threshold: float = 0.7,
):
    """
    차량 좌표 중 dist_threshold(m) 이내인 비율이 ratio_threshold 이상이면 같은 도로로 판단
    """
    if not ambulance_route or not car_points:
        return False, []

    close_count = 0
    near_idx_list = []

    for p in car_points:
        d, idx = point_to_polyline_distance(p["lat"], p["lng"], ambulance_route)
        near_idx_list.append(idx)
        if d <= dist_threshold:
            close_count += 1

    ratio = close_count / len(car_points)
    return ratio >= ratio_threshold, near_idx_list


def bearing(lat1, lon1, lat2, lon2):
    """
    방향각 계산 (0~360)
    """
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dlambda = math.radians(lon2 - lon1)

    x = math.sin(dlambda) * math.cos(phi2)
    y = math.cos(phi1) * math.sin(phi2) - math.sin(phi1)*math.cos(phi2)*math.cos(dlambda)
    brng = math.degrees(math.atan2(x, y))
    return (brng + 360) % 360


def angle_diff(a, b):
    diff = abs(a - b) % 360
    if diff > 180:
        diff = 360 - diff
    return diff


def is_same_direction(
    ambulance_route: List[dict],
    car_points: List[dict],
    nearest_indices: List[int],
    angle_threshold: float = 45.0,
):
    if len(car_points) < 2 or not ambulance_route or not nearest_indices:
        return False

    # 차량 방향
    p1, p2 = car_points[-2], car_points[-1]
    car_heading = bearing(p1["lat"], p1["lng"], p2["lat"], p2["lng"])

    # 구급차 방향
    idx = nearest_indices[-1]
    if idx >= len(ambulance_route) - 1:
        idx = len(ambulance_route) - 2

    a1 = ambulance_route[idx]
    a2 = ambulance_route[idx + 1]
    amb_heading = bearing(a1["lat"], a1["lng"], a2["lat"], a2["lng"])

    diff = angle_diff(car_heading, amb_heading)
    return diff <= angle_threshold


def check_same_road_and_direction(ambulance_route, car_points):
    same_road, idxs = is_on_same_road(ambulance_route, car_points)
    if not same_road:
        return False, False

    same_dir = is_same_direction(ambulance_route, car_points, idxs)
    return same_road, same_dir


def get_any_ambulance_route():
    """
    구급차가 여러 대여도 일단 첫 번째 경로 반환
    """
    for car_no, route in ambulance_routes.items():
        if route:
            return car_no, route
    return None, None
