// static/js/vehicle_log.js
console.log("vehicle_log.js 로드됨");

const LOG_WS_HOST = window.location.hostname;        // 127.0.0.1 같은 값
const logSocket   = new WebSocket(`ws://${LOG_WS_HOST}:5000`);

// ✅ localStorage 키
const STORAGE_KEY = "vehicle_log_active";

// 메모리 캐시: { "<car>__<start_time>": { carNo, startTime, arrivalTime, startLocation, destination } }
let logCache = {};

// ---------------------
//  localStorage 유틸
// ---------------------
function makeKey(carNo, startTime) {
  return `${carNo}__${startTime}`;
}

// ✅ localStorage에는 "도착 안 한 주행"만 저장
function saveCache() {
  try {
    const toPersist = {};
    for (const [key, log] of Object.entries(logCache)) {
      // arrivalTime이 없거나 "-"면 아직 진행 중인 주행으로 간주
      if (!log.arrivalTime || log.arrivalTime === "-") {
        toPersist[key] = log;
      }
    }
    localStorage.setItem(STORAGE_KEY, JSON.stringify(toPersist));
  } catch (e) {
    console.warn("⚠️ vehicle_log localStorage 저장 실패:", e);
  }
}

function loadCache() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed === "object") {
      return parsed;
    }
  } catch (e) {
    console.warn("⚠️ vehicle_log localStorage 파싱 실패:", e);
  }
  return {};
}

// ---------------------
//  테이블 렌더링
// ---------------------
function renderTableFromCache() {
  const table = document.getElementById("vehicle-log-body");
  if (!table) return;

  table.innerHTML = "";  // 전체 비우고 다시 그림

  const entries = Object.values(logCache);

  // 최신 출발이 위로 오게 정렬 (startTime 기준 내림차순)
  entries.sort((a, b) => {
    if (!a.startTime) return 1;
    if (!b.startTime) return -1;
    return a.startTime < b.startTime ? 1 : -1;
  });

  for (const log of entries.slice(0, 10)) {
    const row = document.createElement("tr");
    row.innerHTML = `
      <td>${log.carNo || "-"}</td>
      <td>${log.startTime || "-"}</td>
      <td>${log.arrivalTime || "-"}</td>
      <td>${log.startLocation || "-"}</td>
      <td>${log.destination || "-"}</td>
    `;
    table.appendChild(row);
  }
}

// ---------------------
//  페이지 로드시 복원
// ---------------------
document.addEventListener("DOMContentLoaded", () => {
  localStorage.removeItem(STORAGE_KEY);  // ← 이 줄 추가
  logCache = loadCache();
  console.log("📂 vehicle_log 복원:", logCache);
  renderTableFromCache();
});

// ---------------------
//  WebSocket 처리
// ---------------------
logSocket.onopen = () => {
  console.log("✅ vehicle_log.js WebSocket 연결됨");
};

logSocket.onclose = (ev) => {
  console.warn("❌ vehicle_log.js WebSocket 끊김:", ev.code, ev.reason);
};

logSocket.onerror = (err) => {
  console.error("⚠️ vehicle_log.js WebSocket 에러:", err);
};

logSocket.onmessage = (event) => {
  let data;
  try {
    data = JSON.parse(event.data);
  } catch (e) {
    console.warn("⚠️ vehicle_log.js JSON 파싱 실패:", e);
    return;
  }

  const ev = data.event || data.type;

  if (ev === "ambulance_start" || ev === "ambulance_arrival") {
    console.log("✅ vehicle_log 업데이트 실행:", ev);
    updateOrPrependLogRow(data);
  }
};

// ---------------------
//  테이블 + 캐시 갱신
// ---------------------
function updateOrPrependLogRow(log) {
  const carNo     = log.vehicle_id || log.car;
  const startTime = log.departure_time || log.start_time;

  if (!carNo || !startTime) {
    console.warn("⚠️ vehicle_log: carNo 또는 startTime 없음:", log);
    return;
  }

  const key = makeKey(carNo, startTime);
  const ev  = log.event || log.type;

  if (ev === "ambulance_start") {
    // ✅ 출발: 캐시에 추가/갱신
    logCache[key] = {
      carNo,
      startTime,
      arrivalTime: log.arrival_time || log.estimated_arrival_time || log.eta_time || "-",
      startLocation: log.start_location || log.origin || "-",
      destination: log.destination || log.dest || "-",
    };

  } else if (ev === "ambulance_arrival") {
    // ✅ 도착: 캐시에서는 남겨두고 arrivalTime만 채움
    const arrivalTime =
      log.arrival_time || log.arrivalTime || log.time || "-";

    if (logCache[key]) {
      logCache[key].arrivalTime = arrivalTime;
    } else {
      // 출발 이벤트를 못 받은 상태에서 도착만 온 경우 방어
      logCache[key] = {
        carNo,
        startTime,
        arrivalTime,
        startLocation: log.start_location || log.origin || "-",
        destination: log.destination || log.dest || "-",
      };
    }
  }

  // 변경사항 저장 + 테이블 다시 그리기
  saveCache();
  renderTableFromCache();
}
