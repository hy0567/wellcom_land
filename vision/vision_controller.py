"""
Vision 파이프라인 오케스트레이터
FrameCapture → YOLODetector → DetectionOverlay / ActionDispatcher / VisionLogger
전체 흐름을 연결하고 라이프사이클을 관리
"""

from typing import Optional

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWebEngineWidgets import QWebEngineView

from .frame_capture import FrameCapture
from .detector import YOLODetector, DetectionResult
from .overlay_widget import DetectionOverlay
from .action_dispatcher import ActionDispatcher
from .logger import VisionLogger


class VisionController(QObject):
    """Vision 파이프라인 전체를 제어하는 오케스트레이터"""

    status_changed = pyqtSignal(str)       # "stopped", "loading", "running", "error"
    detection_update = pyqtSignal(object)   # DetectionResult
    stats_update = pyqtSignal(dict)         # logger stats

    def __init__(
        self,
        web_view: QWebEngineView,
        overlay: DetectionOverlay,
        hid_controller=None,
        log_dir: str = "logs",
    ):
        super().__init__()
        self._status = "stopped"

        # 컴포넌트 초기화
        self._capture = FrameCapture(web_view)
        self._detector = YOLODetector()
        self._overlay = overlay
        self._dispatcher = ActionDispatcher(hid_controller)
        self._logger = VisionLogger(log_dir)

        # 시그널 연결
        self._capture.frame_ready.connect(self._on_frame_ready)
        self._detector.detection_done.connect(self._on_detection)
        self._detector.model_loaded.connect(self._on_model_loaded)
        self._detector.error_occurred.connect(self._on_error)

    # ─── 공개 API ────────────────────────────────────────

    def load_model(self, model_path: str):
        """YOLO 모델 로드"""
        self._set_status("loading")
        success = self._detector.load_model(model_path)
        if not success:
            self._set_status("error")

    def start(self):
        """파이프라인 시작 (모델이 로드된 상태여야 함)"""
        if not self._detector.is_model_loaded:
            self._set_status("error")
            return

        self._detector.start()
        self._capture.start()
        self._set_status("running")

    def stop(self):
        """파이프라인 중지"""
        self._capture.stop()
        self._detector.stop()
        self._overlay.clear()
        self._set_status("stopped")

    def set_model(self, model_path: str):
        """모델 변경 (실행 중이면 재시작)"""
        was_running = self._status == "running"
        if was_running:
            self.stop()

        self.load_model(model_path)

        if was_running and self._detector.is_model_loaded:
            self.start()

    def set_fps(self, fps: int):
        """캡처 FPS 변경"""
        self._capture.set_fps(fps)

    def set_confidence(self, threshold: float):
        """감지 신뢰도 임계값 변경"""
        self._detector.set_confidence(threshold)

    def set_auto_action(self, enabled: bool):
        """자동 HID 액션 활성화/비활성화"""
        self._dispatcher.set_enabled(enabled)

    def set_overlay_enabled(self, enabled: bool):
        """오버레이 표시 on/off"""
        self._overlay.setVisible(enabled)
        if not enabled:
            self._overlay.clear()

    def set_log_enabled(self, enabled: bool):
        """로깅 활성화/비활성화"""
        self._logger.set_enabled(enabled)

    def set_hid_controller(self, hid_controller):
        """HID 컨트롤러 설정/변경"""
        self._dispatcher.set_hid_controller(hid_controller)

    def load_action_rules(self, rule_dicts: list):
        """액션 규칙 로드 (딕셔너리 목록)"""
        self._dispatcher.load_rules_from_dicts(rule_dicts)

    def get_stats(self) -> dict:
        """현재 세션 통계"""
        return self._logger.get_stats()

    def get_model_names(self) -> dict:
        """로드된 모델의 클래스 이름"""
        return self._detector.model_names

    @property
    def status(self) -> str:
        return self._status

    @property
    def is_running(self) -> bool:
        return self._status == "running"

    # ─── 내부 핸들러 ────────────────────────────────────────

    def _on_frame_ready(self, frame):
        """캡처된 프레임을 detector 큐에 전달"""
        self._detector.submit_frame(frame)

    def _on_detection(self, result: DetectionResult):
        """감지 결과 처리: 오버레이 업데이트 + 액션 실행 + 로깅"""
        # 오버레이 업데이트
        self._overlay.update_detections(result)

        # 액션 실행
        actions = self._dispatcher.process(result)

        # 로깅
        self._logger.log(result, actions if actions else None)

        # 외부 시그널
        self.detection_update.emit(result)

    def _on_model_loaded(self, success: bool, message: str):
        """모델 로드 결과"""
        if success:
            self._set_status("stopped")
        else:
            self._set_status("error")
        print(f"[Vision] {message}")

    def _on_error(self, error_msg: str):
        """오류 처리"""
        print(f"[Vision] Error: {error_msg}")

    def _set_status(self, status: str):
        """상태 변경 및 시그널 발생"""
        self._status = status
        self.status_changed.emit(status)

    def cleanup(self):
        """리소스 정리"""
        self.stop()
        self._logger.close()
