// static/js/camera.js

console.log("camera.js 로드됨");

// 🔌 WebSocket 주소: 현재 접속한 호스트 기준으로 맞추기
//   대시보드가 http://127.0.0.1:8000 이면 → ws://127.0.0.1:5000
// 🔌 카메라용 WebSocket host
const CAM_WS_HOST = window.location.hostname;
const camSocket = new WebSocket(`ws://${CAM_WS_HOST}:5000`);

camSocket.onopen = () => {
  console.log("✅ camera.js WebSocket 연결됨");
};

camSocket.onclose = (ev) => {
  console.warn("❌ camera.js WebSocket 끊김:", ev.code, ev.reason);
  showNoSignal();
};

camSocket.onerror = (err) => {
  console.error("⚠️ camera.js WebSocket 에러:", err);
};

// ------------------------
//  No signal 기본 이미지
// ------------------------
let cam1Timeout;
const CAM1_TIMEOUT_MS = 5000;

function showNoSignal() {
  const img = document.getElementById("cam1");
  if (img) {
    img.src = "/static/images/no_signal.png";
  }
}
showNoSignal();  // 초기에 한 번

// ------------------------
//  메시지 수신 처리
// ------------------------
camSocket.onmessage = (event) => {
  // 🔎 디버깅용: 앞쪽 120글자만 찍기
  // console.log("📩 camera.js 수신 raw:", event.data.slice(0, 120));

  let data;
  try {
    data = JSON.parse(event.data);
  } catch (e) {
    // JSON이 아니면 "그냥 base64 이미지"라고 가정
    // console.warn("⚠️ JSON 아님 → base64로 처리", e);
    setCameraFrame(event.data);
    return;
  }

  const ev = data.event || data.type;
  // console.log("🎯 camera.js parsed event =", ev);

  // 서버에서 보내는 형식: { "event": "video", "car": "...", "frame": "..." }
  if (ev === "video" || ev === "image_broadcast_cam1") {
    const base64image = data.frame || data.image;
    if (!base64image) {
      // console.warn("⚠️ video 이벤트인데 frame/image 없음");
      return;
    }
    // console.log("🎥 camera.js frame 업데이트 실행");
    setCameraFrame(base64image);
  } else {
    // 다른 이벤트는 무시
    // console.log("camera.js: 영상 아닌 이벤트 무시:", ev);
  }
};

function setCameraFrame(base64image) {
  const img = document.getElementById("cam1");
  if (!img) {
    console.warn("⚠️ #cam1 이미지 태그를 찾을 수 없음");
    return;
  }

  // 실제 프레임 반영
  img.src = "data:image/jpeg;base64," + base64image;

  // 5초 동안 새 프레임 없으면 no_signal로 복귀
  clearTimeout(cam1Timeout);
  cam1Timeout = setTimeout(showNoSignal, CAM1_TIMEOUT_MS);
}
