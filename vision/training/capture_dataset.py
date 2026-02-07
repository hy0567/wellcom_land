"""
YOLO 학습용 데이터셋 자동 수집 스크립트
KVM WebView에서 게임플레이 화면을 주기적으로 캡처하여 이미지로 저장

사용법:
    python vision/training/capture_dataset.py --ip 192.168.0.113 --interval 3 --output dataset/images/raw
"""

import os
import sys
import time
import argparse
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def try_http_snapshot(ip: str, port: int) -> str:
    """HTTP 스냅샷 엔드포인트가 있는지 확인. 이미지 Content-Type만 허용."""
    import requests

    urls = [
        f"http://{ip}:{port}/api/stream/snapshot",
        f"http://{ip}:{port}/snapshot",
        f"http://{ip}:{port}/capture",
    ]
    for url in urls:
        try:
            resp = requests.get(url, timeout=3)
            ct = resp.headers.get("Content-Type", "")
            if resp.status_code == 200 and ct.startswith("image/"):
                return url
        except Exception:
            continue
    return ""


def capture_via_pyqt(ip: str, port: int, output_dir: str, interval: float, count: int):
    """
    PyQt6 WebEngine 기반 캡처
    KVM 웹페이지를 렌더링하고 WebRTC 비디오 화면을 캡처
    """
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (
        "--enable-features=WebRTCPipeWireCapturer "
        "--autoplay-policy=no-user-gesture-required "
        "--enable-accelerated-video-decode"
    )

    from PyQt6.QtWidgets import QApplication, QLabel, QPushButton, QVBoxLayout, QHBoxLayout, QWidget
    from PyQt6.QtCore import QUrl, QTimer, Qt
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    from PyQt6.QtWebEngineCore import QWebEngineSettings

    os.makedirs(output_dir, exist_ok=True)

    app = QApplication(sys.argv)

    # 메인 윈도우
    window = QWidget()
    window.setWindowTitle(f"YOLO 데이터 수집 - {ip}")
    window.resize(1300, 800)
    layout = QVBoxLayout(window)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)

    # 상태 바
    status_bar = QWidget()
    status_layout = QHBoxLayout(status_bar)
    status_layout.setContentsMargins(8, 4, 8, 4)
    status_label = QLabel("페이지 로드 중...")
    status_label.setStyleSheet("color: #fff; font-weight: bold; font-size: 12px;")
    status_layout.addWidget(status_label)
    status_layout.addStretch()

    count_label = QLabel("캡처: 0장")
    count_label.setStyleSheet("color: #4CAF50; font-weight: bold; font-size: 12px;")
    status_layout.addWidget(count_label)

    btn_pause = QPushButton("일시정지")
    btn_pause.setStyleSheet("padding: 4px 12px; font-size: 11px;")
    status_layout.addWidget(btn_pause)

    status_bar.setStyleSheet("background-color: #1a1a1a;")
    status_bar.setFixedHeight(32)
    layout.addWidget(status_bar)

    # WebView
    web_view = QWebEngineView()
    s = web_view.settings()
    s.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
    s.setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False)
    s.setAttribute(QWebEngineSettings.WebAttribute.Accelerated2dCanvasEnabled, True)
    s.setAttribute(QWebEngineSettings.WebAttribute.WebGLEnabled, True)

    url = f"http://{ip}:{port}"
    web_view.setUrl(QUrl(url))
    layout.addWidget(web_view, 1)

    saved_count = [0]
    paused = [False]
    capture_timer = [None]

    # PicoKVM UI 정리 JS (비디오만 전체화면)
    CLEAN_JS = """
    (function() {
        var style = document.createElement('style');
        style.textContent = `
            header, footer, aside, nav, .header, .footer, .sidebar,
            [class*="header"], [class*="footer"], [class*="sidebar"],
            [class*="toolbar"], [class*="menu"], [class*="status"] {
                display: none !important;
            }
            video, canvas {
                position: fixed !important;
                top: 0 !important; left: 0 !important;
                width: 100vw !important; height: 100vh !important;
                object-fit: contain !important;
                z-index: 9999 !important;
            }
            body { background: #000 !important; overflow: hidden !important; margin: 0 !important; }
        `;
        document.head.appendChild(style);
        var v = document.querySelector('video') || document.querySelector('canvas');
        if (v) { document.body.appendChild(v); return true; }
        return false;
    })();
    """

    def on_loaded(ok):
        if ok:
            status_label.setText(f"연결됨: {url}")
            # 비디오 스트림이 시작될 때까지 대기 후 UI 정리 및 캡처 시작
            QTimer.singleShot(2000, clean_and_start)

    def clean_and_start():
        web_view.page().runJavaScript(CLEAN_JS, on_clean_result)

    def on_clean_result(result):
        if not result:
            QTimer.singleShot(1000, clean_and_start)
            return
        status_label.setText(f"수집 중 - {interval}초 간격 | {url}")
        start_capture()

    def start_capture():
        timer = QTimer()

        def capture():
            if paused[0]:
                return
            pixmap = web_view.grab()
            if not pixmap.isNull() and pixmap.width() > 100:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
                filename = f"frame_{timestamp}.jpg"
                filepath = os.path.join(output_dir, filename)
                pixmap.save(filepath, "JPEG", 95)
                saved_count[0] += 1
                count_label.setText(f"캡처: {saved_count[0]}장")
                print(f"  [{saved_count[0]}] {filename} ({pixmap.width()}x{pixmap.height()})")

                if 0 < count <= saved_count[0]:
                    timer.stop()
                    status_label.setText(f"완료 - {saved_count[0]}장")
                    print(f"\n[수집] 완료 - {saved_count[0]}장 저장됨")

        timer.timeout.connect(capture)
        timer.start(int(interval * 1000))
        capture_timer[0] = timer

    def toggle_pause():
        paused[0] = not paused[0]
        btn_pause.setText("재개" if paused[0] else "일시정지")
        status_label.setText(
            f"일시정지 | {saved_count[0]}장" if paused[0]
            else f"수집 중 - {interval}초 간격 | {url}"
        )

    btn_pause.clicked.connect(toggle_pause)
    web_view.loadFinished.connect(on_loaded)
    window.show()

    print(f"[수집] PyQt 캡처 모드 시작")
    print(f"[수집] URL: {url}")
    print(f"[수집] 저장: {output_dir}")
    print(f"[수집] 간격: {interval}초, 목표: {count if count > 0 else '무제한'}장")
    print(f"[수집] 창을 닫으면 종료\n")

    app.exec()
    print(f"\n[수집] 종료 - 총 {saved_count[0]}장 저장됨: {output_dir}")


def main():
    parser = argparse.ArgumentParser(description="YOLO 학습용 게임 스크린샷 자동 수집")
    parser.add_argument("--ip", required=True, help="KVM 장치 IP (예: 192.168.0.113)")
    parser.add_argument("--port", type=int, default=80, help="KVM 웹 포트 (기본: 80)")
    parser.add_argument("--output", default="dataset/images/raw", help="저장 경로")
    parser.add_argument("--interval", type=float, default=3.0, help="캡처 간격 (초)")
    parser.add_argument("--count", type=int, default=0, help="캡처 수 (0=무제한)")

    args = parser.parse_args()

    # Luckfox PicoKVM은 HTTP 스냅샷을 지원하지 않으므로 PyQt 모드 사용
    print(f"[수집] HTTP 스냅샷 확인 중...")
    http_url = try_http_snapshot(args.ip, args.port)
    if http_url:
        print(f"[수집] HTTP 스냅샷 발견: {http_url}")
        print(f"[수집] 그러나 PyQt 모드가 더 안정적입니다.")

    print(f"[수집] PyQt WebEngine 캡처 모드로 시작합니다.\n")
    capture_via_pyqt(args.ip, args.port, args.output, args.interval, args.count)


if __name__ == "__main__":
    main()
