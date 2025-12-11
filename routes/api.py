# routes/api.py
from flask import Blueprint, jsonify, request
from sockets.ambulance_state import (
    get_ambulance_position,
    get_all_ambulance_positions,
)

bp = Blueprint("api", __name__, url_prefix="/api")


@bp.get("/ambulance/position")
def get_ambulance_position_api():
    """
    /api/ambulance/position?car=1234 이런 식으로 요청하면,
    해당 구급차의 최신 좌표를 JSON으로 반환
    """
    car_no = request.args.get("car")
    if car_no:
        pos = get_ambulance_position(car_no)
        if pos is None:
            return jsonify({"error": "no_position", "car": car_no}), 404
        return jsonify(pos)
    else:
        # car 파라미터 없으면 전체 목록 반환
        return jsonify(get_all_ambulance_positions())
