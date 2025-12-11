# sockets/ambulance_state.py
from datetime import datetime
from typing import Dict, Any, Optional

# 차량별 최신 위치
_latest_positions: Dict[str, Dict[str, Any]] = {}


def update_ambulance_position(car_no: str, lat: float, lng: float,
                              speed: float | None = None,
                              lane: int | None = None,   
                              ts: datetime | None = None) -> None:
    """WS에서 받은 최신 위치를 저장"""
    global _latest_positions
    _latest_positions[car_no] = {
        "car": car_no,
        "lat": lat,
        "lng": lng,
        "speed": speed,
        "lane": lane, 
        "timestamp": (ts or datetime.now()).isoformat(),
    }


def get_ambulance_position(car_no: str) -> Optional[Dict[str, Any]]:
    """특정 차량의 최신 위치 반환"""
    return _latest_positions.get(car_no)


def get_all_ambulance_positions() -> Dict[str, Dict[str, Any]]:
    """모든 차량의 최신 위치 반환"""
    return _latest_positions
