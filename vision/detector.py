"""
YOLOv8 추론 엔진
별도 QThread에서 YOLO 모델 추론을 실행하여 UI 블로킹 방지
"""

import queue
import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from PyQt6.QtCore import QThread, pyqtSignal


@dataclass
class DetectionResult:
    """단일 프레임의 감지 결과"""
    boxes: list = field(default_factory=list)       # [[x1, y1, x2, y2, conf, class_id], ...]
    labels: list = field(default_factory=list)       # ["enemy", "hp_bar", ...]
    frame_shape: tuple = (0, 0)                      # (height, width)
    timestamp: float = 0.0
    inference_ms: float = 0.0


class YOLODetector(QThread):
    """별도 스레드에서 YOLO 추론 실행"""

    detection_done = pyqtSignal(object)  # DetectionResult
    model_loaded = pyqtSignal(bool, str)  # success, message
    error_occurred = pyqtSignal(str)

    def __init__(self, model_path: str = "", conf_threshold: float = 0.5, device: str = "auto"):
        super().__init__()
        self._model_path = model_path
        self._conf_threshold = conf_threshold
        self._device = device
        self._model = None
        self._frame_queue: queue.Queue = queue.Queue(maxsize=2)
        self._running = False

    def load_model(self, model_path: str) -> bool:
        """YOLO 모델 로드"""
        try:
            from ultralytics import YOLO
            self._model_path = model_path
            self._model = YOLO(model_path)

            # 디바이스 설정
            if self._device == "auto":
                import torch
                device = "cuda" if torch.cuda.is_available() else "cpu"
            else:
                device = self._device

            # 워밍업 (더미 추론으로 초기화)
            dummy = np.zeros((640, 640, 3), dtype=np.uint8)
            self._model.predict(dummy, device=device, verbose=False)

            self.model_loaded.emit(True, f"모델 로드 완료: {model_path} ({device})")
            return True

        except ImportError:
            msg = "ultralytics 패키지가 설치되지 않았습니다. pip install ultralytics"
            self.model_loaded.emit(False, msg)
            return False
        except Exception as e:
            msg = f"모델 로드 실패: {e}"
            self.model_loaded.emit(False, msg)
            return False

    def set_confidence(self, threshold: float):
        """신뢰도 임계값 변경"""
        self._conf_threshold = max(0.01, min(1.0, threshold))

    def set_device(self, device: str):
        """추론 디바이스 변경 (다음 모델 로드 시 적용)"""
        self._device = device

    def submit_frame(self, frame: np.ndarray):
        """추론할 프레임을 큐에 추가 (오래된 프레임은 drop)"""
        if not self._running or self._model is None:
            return

        # 큐가 가득 차면 오래된 프레임 제거
        if self._frame_queue.full():
            try:
                self._frame_queue.get_nowait()
            except queue.Empty:
                pass

        try:
            self._frame_queue.put_nowait(frame)
        except queue.Full:
            pass

    def run(self):
        """워커 루프: 큐에서 프레임을 꺼내 YOLO 추론 실행"""
        self._running = True

        while self._running:
            try:
                frame = self._frame_queue.get(timeout=0.1)
            except queue.Empty:
                continue

            if self._model is None:
                continue

            try:
                t0 = time.perf_counter()

                # 디바이스 결정
                if self._device == "auto":
                    try:
                        import torch
                        device = "cuda" if torch.cuda.is_available() else "cpu"
                    except ImportError:
                        device = "cpu"
                else:
                    device = self._device

                results = self._model.predict(
                    frame,
                    conf=self._conf_threshold,
                    device=device,
                    verbose=False,
                )

                inference_ms = (time.perf_counter() - t0) * 1000

                # 결과 파싱
                detection = DetectionResult(
                    frame_shape=(frame.shape[0], frame.shape[1]),
                    timestamp=time.time(),
                    inference_ms=inference_ms,
                )

                if results and len(results) > 0:
                    result = results[0]
                    if result.boxes is not None and len(result.boxes) > 0:
                        for box in result.boxes:
                            x1, y1, x2, y2 = box.xyxy[0].tolist()
                            conf = float(box.conf[0])
                            cls_id = int(box.cls[0])
                            cls_name = self._model.names.get(cls_id, str(cls_id))

                            detection.boxes.append([x1, y1, x2, y2, conf, cls_id])
                            detection.labels.append(cls_name)

                self.detection_done.emit(detection)

            except Exception as e:
                self.error_occurred.emit(f"추론 오류: {e}")

    def stop(self):
        """워커 중지"""
        self._running = False
        self.wait(2000)

    @property
    def is_model_loaded(self) -> bool:
        return self._model is not None

    @property
    def model_names(self) -> dict:
        """모델의 클래스 이름 딕셔너리"""
        if self._model is not None:
            return dict(self._model.names)
        return {}
