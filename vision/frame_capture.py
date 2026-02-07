"""
QWebEngineView 프레임 캡처
WebEngineView에서 주기적으로 화면을 캡처하여 numpy 배열로 변환
"""

import numpy as np
from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtGui import QImage
from PyQt6.QtWebEngineWidgets import QWebEngineView


class FrameCapture(QObject):
    """QWebEngineView에서 프레임을 캡처하여 numpy 배열로 변환"""

    frame_ready = pyqtSignal(object)  # numpy ndarray (BGR)

    def __init__(self, web_view: QWebEngineView, fps: int = 2):
        super().__init__()
        self._web_view = web_view
        self._fps = fps
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._capture_frame)
        self._running = False

    def start(self):
        """캡처 시작"""
        if self._running:
            return
        self._running = True
        interval_ms = max(50, int(1000 / self._fps))
        self._timer.start(interval_ms)

    def stop(self):
        """캡처 중지"""
        self._running = False
        self._timer.stop()

    def set_fps(self, fps: int):
        """캡처 속도 변경"""
        self._fps = max(1, min(30, fps))
        if self._running:
            interval_ms = max(50, int(1000 / self._fps))
            self._timer.setInterval(interval_ms)

    @property
    def is_running(self) -> bool:
        return self._running

    def _capture_frame(self):
        """WebEngineView에서 프레임 캡처 후 numpy 변환"""
        if not self._running or not self._web_view.isVisible():
            return

        try:
            pixmap = self._web_view.grab()
            if pixmap.isNull():
                return

            image = pixmap.toImage().convertToFormat(QImage.Format.Format_RGB888)
            width = image.width()
            height = image.height()

            if width == 0 or height == 0:
                return

            ptr = image.bits()
            if ptr is None:
                return
            ptr.setsize(height * width * 3)

            frame = np.frombuffer(ptr, dtype=np.uint8).reshape((height, width, 3))
            # RGB → BGR (OpenCV/YOLO 표준)
            frame = frame[:, :, ::-1].copy()

            self.frame_ready.emit(frame)

        except Exception as e:
            print(f"[Vision] Frame capture error: {e}")
