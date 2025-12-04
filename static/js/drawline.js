// drawline_ws.js
var DrawLine = DrawLine || {};

DrawLine.map = null;
DrawLine.vehicleMarker = null;
DrawLine.routeLines = [];
DrawLine.carMarker = null;

// ✅ 교차로 마커 저장용: { crossroad_id: { marker, status, lat, lng } }
DrawLine.crossroadMarkers = {};

// ✅ 교차로 아이콘 경로
const CROSS_ICON_BLUE = "/static/images/crossroad_blue.png"; // 기본
const CROSS_ICON_RED  = "/static/images/crossroad_red.png";  // 접근/도착

const TMAP_APP_KEY = "73xHlMiaGI39dgyBwYeO55jUPwFiKn4027JN3ntC";

// ✅ 브라우저에 상태 저장용 key
const STORAGE_KEY_ROUTE       = "dashboard_current_route";
const STORAGE_KEY_CROSSROADS  = "dashboard_crossroads";

// 🔥 마지막으로 구급차 위치를 받은 시각 (ms)
DrawLine.lastAmbulanceUpdate = null;

// 🔥 이 시간 이상 위치 업데이트 없으면 "죽었다"고 보고 정리 (예: 30초)
const AMBULANCE_TIMEOUT_MS = 30000;

// --------------------------------------------------------
//  초기화
// --------------------------------------------------------
$(function () {
    DrawLine.initMap();

    // 🔄 새로고침 때 로컬스토리지에서 복구
    DrawLine.restoreFromStorage();

    setTimeout(() => {
        // ✅ WebSocket 주소 동적으로
        const DRAW_WS_HOST = window.location.hostname;
        const socket = new WebSocket(`ws://${DRAW_WS_HOST}:5000`);

        socket.onopen = () => {
            console.log("✅ drawline.js WebSocket 연결됨");
        };

        socket.onclose = () => {
            console.warn("❌ drawline.js WebSocket 끊김");
        };

        socket.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                const ev = data.event || data.type;
                // console.log("📩 drawline.js 수신:", ev, data);

                // ---------------- 일반 차량 위치 ----------------
                if (ev === "normalcar_current") {
                    if (data.current) {
                        DrawLine.updateCarMarker(
                            data.current.lat,
                            data.current.lng
                        );
                    }
                }

                // ---------------- 구급차 경로 ----------------
                else if (ev === "ambulance_route") {
                    if (data.route_points && data.route_points.length > 0) {
                        const linePoints = data.route_points.map(
                            p => new Tmapv2.LatLng(p.lat, p.lng)
                        );
                        DrawLine.clearRoute();
                        DrawLine.drawLine(linePoints, "#0000FF");
                        DrawLine.setMapBound(linePoints);

                        // ✅ 경로를 로컬스토리지에 저장
                        DrawLine.saveRoute(data.route_points);
                    }
                }

                // ---------------- 구급차 현재 위치 ----------------
                else if (ev === "ambulance_current") {
                    if (data.current) {
                        DrawLine.updateVehicleMarker(
                            data.current.lat,
                            data.current.lng
                        );
                    }

                    // ✅ 마지막 위치 업데이트 시각 갱신
                    DrawLine.lastAmbulanceUpdate = Date.now();
                }

                // ---------------- 구급차 출발/도착 ----------------
                else if (ev === "ambulance_arrival") {
                    console.log("🏁 도착 알림 수신:", data);

                    // 마커 & 경로 제거
                    if (DrawLine.vehicleMarker) {
                        DrawLine.vehicleMarker.setMap(null);
                        DrawLine.vehicleMarker = null;
                    }

                    DrawLine.clearRoute();
                    DrawLine.clearCrossroads();

                    // ✅ 도착하면 저장된 상태도 초기화
                    localStorage.removeItem(STORAGE_KEY_ROUTE);
                    localStorage.removeItem(STORAGE_KEY_CROSSROADS);

                    // 상태 리셋
                    DrawLine.lastAmbulanceUpdate = null;
                }

                // ---------------- 예상 교차로(경로 기준) ----------------
                // 서버: ambulance_expected_crossroads
                else if (ev === "ambulance_expected_crossroads") {
                    const list = data.crossroads || [];
                    console.log("🚦 예상 교차로 목록:", list);

                    DrawLine.clearCrossroads();

                    list.forEach((c) => {
                        const id  = c.id || c.crossroad_id;
                        const lat = c.lat;
                        const lng = c.lon || c.lng;   // 서버에서 lon 쓰면 대비

                        DrawLine.createOrUpdateCrossroadMarker(
                            id,
                            lat,
                            lng,
                            c.status || "pending"
                        );
                    });

                    // ✅ 교차로 상태 저장
                    DrawLine.saveCrossroads();
                }

                // ---------------- 교차로 접근 ----------------
                // 서버: ambulance_crossroad_approach
                else if (ev === "ambulance_crossroad_approach") {
                    const id = data.crossroad_id || data.id;
                    console.log("⚠️ 교차로 접근:", id, data);

                    const info = DrawLine.crossroadMarkers[id];
                    const lat = (info && info.lat) || data.lat || data.y;
                    const lng = (info && info.lng) || data.lng || data.lon || data.x;

                    DrawLine.createOrUpdateCrossroadMarker(id, lat, lng, "approaching");
                    DrawLine.saveCrossroads();
                }

                // ---------------- 교차로 도착 ----------------
                // 서버: ambulance_crossroad_arrived
                else if (ev === "ambulance_crossroad_arrived") {
                    const id = data.crossroad_id || data.id;
                    console.log("🚦 교차로 도착:", id, data);
                    DrawLine.setCrossroadStatus(id, "arrived");
                    DrawLine.saveCrossroads();
                }

                // ---------------- 교차로 통과 ----------------
                // 서버: ambulance_crossroad_passed
                else if (ev === "ambulance_crossroad_passed") {
                    const id = data.crossroad_id || data.id;
                    console.log("✅ 교차로 통과:", id, data);
                    DrawLine.setCrossroadStatus(id, "passed");
                    DrawLine.saveCrossroads();
                }

            } catch (e) {
                console.warn("⚠️ drawline.js onmessage 오류:", e);
            }
        };
    }, 500);

    // 🔥 5초마다 한 번씩, 응급차 타임아웃 체크
    setInterval(() => {
        // 경로가 없으면 검사할 필요 없음
        if (!DrawLine.routeLines || DrawLine.routeLines.length === 0) {
            return;
        }

        // 아직 한 번도 위치를 못 받은 상태면 패스
        if (!DrawLine.lastAmbulanceUpdate) {
            return;
        }

        const now = Date.now();
        const diff = now - DrawLine.lastAmbulanceUpdate;

        if (diff > AMBULANCE_TIMEOUT_MS) {
            console.warn("⛔ 구급차 위치 업데이트 끊김 → 강제 종료 처리");

            // 지도에서 싹 정리
            DrawLine.clearRoute();
            DrawLine.clearCrossroads();

            if (DrawLine.vehicleMarker) {
                DrawLine.vehicleMarker.setMap(null);
                DrawLine.vehicleMarker = null;
            }

            // localStorage 정리
            localStorage.removeItem(STORAGE_KEY_ROUTE);
            localStorage.removeItem(STORAGE_KEY_CROSSROADS);

            // 상태 리셋
            DrawLine.lastAmbulanceUpdate = null;
        }
    }, 5000); // 5초마다 체크
});

// --------------------------------------------------------
//  지도 / 기본 마커
// --------------------------------------------------------
DrawLine.initMap = function () {
    DrawLine.map = new Tmapv2.Map("map_div", {
        width: "100%",
        height: "500px",
        zoomControl: true,
        scrollwheel: true,
    });
};

DrawLine.updateVehicleMarker = function (lat, lng) {
    if (!DrawLine.map) {
        console.warn("⚠️ 지도 객체가 아직 초기화되지 않았습니다.");
        return;
    }

    if (!DrawLine.vehicleMarker) {
        DrawLine.vehicleMarker = new Tmapv2.Marker({
            position: new Tmapv2.LatLng(lat, lng),
            icon: "/static/images/ambulance.png",
            iconSize: new Tmapv2.Size(30, 30),
            map: DrawLine.map,
        });
    } else {
        if (!DrawLine.vehicleMarker.getMap()) {
            DrawLine.vehicleMarker.setMap(DrawLine.map);
        }
        if (lat && lng) {
            try {
                DrawLine.vehicleMarker.setPosition(
                    new Tmapv2.LatLng(lat, lng)
                );
            } catch (e) {
                console.warn("⚠️ 마커 업데이트 실패:", e);
            }
        }
    }
};

DrawLine.updateCarMarker = function (lat, lng) {
    if (!DrawLine.map) {
        console.warn("⚠️ 지도 객체가 아직 초기화되지 않았습니다.");
        return;
    }

    if (!DrawLine.carMarker) {
        DrawLine.carMarker = new Tmapv2.Marker({
            position: new Tmapv2.LatLng(lat, lng),
            icon: "/static/images/car.png",
            iconSize: new Tmapv2.Size(40, 40),
            map: DrawLine.map,
        });
    } else {
        if (!DrawLine.carMarker.getMap()) {
            DrawLine.carMarker.setMap(DrawLine.map);
        }
        if (lat && lng) {
            try {
                DrawLine.carMarker.setPosition(
                    new Tmapv2.LatLng(lat, lng)
                );
            } catch (e) {
                console.warn("⚠️ 일반 차량 마커 업데이트 실패:", e);
            }
        }
    }
};

// --------------------------------------------------------
//  경로
// --------------------------------------------------------
DrawLine.drawLine = function (pointList, lineColor) {
    if (!pointList || pointList.length < 2) return;
    const polyline = new Tmapv2.Polyline({
        path: pointList,
        strokeColor: lineColor,
        strokeWeight: 6,
        map: DrawLine.map,
    });
    DrawLine.routeLines.push(polyline);
};

DrawLine.clearRoute = function () {
    if (DrawLine.routeLines && DrawLine.routeLines.length > 0) {
        DrawLine.routeLines.forEach(line => line.setMap(null));
        DrawLine.routeLines = [];
    }
};

DrawLine.setMapBound = function (pointList) {
    if (!pointList || pointList.length < 2) return;
    const bounds = new Tmapv2.LatLngBounds();
    pointList.forEach(p => bounds.extend(p));
    DrawLine.map.panToBounds(bounds);
};

// --------------------------------------------------------
//  교차로 마커
// --------------------------------------------------------
DrawLine.createOrUpdateCrossroadMarker = function (id, lat, lng, status) {
    if (!DrawLine.map) return;
    if (!lat || !lng) return;

    const pos = new Tmapv2.LatLng(lat, lng);

    const iconPath =
        status === "approaching" || status === "arrived"
            ? CROSS_ICON_RED
            : CROSS_ICON_BLUE;

    const exist = DrawLine.crossroadMarkers[id];
    if (exist && exist.marker) {
        exist.marker.setMap(null);
    }

    const marker = new Tmapv2.Marker({
        position: pos,
        icon: iconPath,
        iconSize: new Tmapv2.Size(40, 40),
        map: DrawLine.map,
    });

    DrawLine.crossroadMarkers[id] = {
        marker: marker,
        status: status || "pending",
        lat: lat,
        lng: lng,
    };
};

DrawLine.setCrossroadStatus = function (id, status) {
    const info = DrawLine.crossroadMarkers[id];
    if (!info) {
        console.warn("⚠️ setCrossroadStatus: 해당 교차로 없음", id);
        return;
    }
    DrawLine.createOrUpdateCrossroadMarker(id, info.lat, info.lng, status);
};

DrawLine.clearCrossroads = function () {
    Object.values(DrawLine.crossroadMarkers).forEach(info => {
        if (info.marker) info.marker.setMap(null);
    });
    DrawLine.crossroadMarkers = {};
};

// --------------------------------------------------------
//  로컬스토리지 저장/복구
// --------------------------------------------------------
DrawLine.saveRoute = function (routePoints) {
    try {
        localStorage.setItem(STORAGE_KEY_ROUTE, JSON.stringify(routePoints));
    } catch (e) {
        console.warn("⚠️ 경로 저장 실패:", e);
    }
};

DrawLine.saveCrossroads = function () {
    try {
        const list = Object.entries(DrawLine.crossroadMarkers).map(([id, info]) => ({
            id: id,
            lat: info.lat,
            lng: info.lng,
            status: info.status,
        }));
        localStorage.setItem(STORAGE_KEY_CROSSROADS, JSON.stringify(list));
    } catch (e) {
        console.warn("⚠️ 교차로 저장 실패:", e);
    }
};

DrawLine.restoreFromStorage = function () {
    try {
        // 1) 경로 복구
        const routeStr = localStorage.getItem(STORAGE_KEY_ROUTE);
        if (routeStr) {
            const routePoints = JSON.parse(routeStr);
            if (routePoints && routePoints.length > 0) {
                const linePoints = routePoints.map(
                    p => new Tmapv2.LatLng(p.lat, p.lng)
                );
                DrawLine.clearRoute();
                DrawLine.drawLine(linePoints, "#0000FF");
                DrawLine.setMapBound(linePoints);
                console.log("🔁 저장된 경로 복구 완료");

                // ✅ 복구 상태에서도 타임아웃 체크가 의미있게 동작하도록
                DrawLine.lastAmbulanceUpdate = Date.now();
            }
        }

        // 2) 교차로 복구
        const crossStr = localStorage.getItem(STORAGE_KEY_CROSSROADS);
        if (crossStr) {
            const list = JSON.parse(crossStr);
            if (Array.isArray(list)) {
                DrawLine.clearCrossroads();
                list.forEach(c => {
                    DrawLine.createOrUpdateCrossroadMarker(
                        c.id,
                        c.lat,
                        c.lng,
                        c.status
                    );
                });
                console.log("🔁 저장된 교차로 복구 완료");
            }
        }
    } catch (e) {
        console.warn("⚠️ 저장된 상태 복구 실패:", e);
    }
};
