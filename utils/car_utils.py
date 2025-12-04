# imbeded_web/utils/car_utils.py

kor_map = {
    "가": "ga", "나": "na", "다": "da", "라": "ra", "마": "ma",
    "바": "ba", "사": "sa", "아": "a",  "자": "ja", "차": "cha",
    "카": "ka", "타": "ta", "파": "pa", "하": "ha"
}

def normalize_car_no(car_no: str) -> str:
    """
    차량번호를 안전한 문자열로 변환
    - 한글은 kor_map 기반으로 영문화
    - 영문/숫자는 그대로 통과
    - 나머지(띄어쓰기, 특수문자)는 '_'로 치환
    """
    safe = ""
    for ch in car_no:
        if ch in kor_map:
            safe += kor_map[ch]
        elif ch.isalnum():
            safe += ch
        else:
            safe += "_"
    return safe
