"""
Vision 감지 이벤트 로거
감지 결과와 액션 이력을 JSON Lines 형식으로 기록
"""

import json
import os
import time
from datetime import datetime
from typing import Optional

from .detector import DetectionResult


class VisionLogger:
    """감지 이벤트를 파일로 기록"""

    def __init__(self, log_dir: str):
        self._log_dir = os.path.join(log_dir, "vision")
        self._enabled = True
        self._file = None
        self._current_date = ""
        self._session_start = time.time()
        self._total_detections = 0
        self._total_actions = 0
        self._total_frames = 0

    def set_enabled(self, enabled: bool):
        self._enabled = enabled
        if not enabled:
            self._close_file()

    def log(self, result: DetectionResult, actions: Optional[list] = None):
        """감지 결과 기록"""
        if not self._enabled:
            return

        self._total_frames += 1
        self._total_detections += len(result.boxes)
        if actions:
            self._total_actions += len(actions)

        # 감지가 없으면 기록 생략 (로그 크기 절약)
        if not result.boxes and not actions:
            return

        entry = {
            "ts": result.timestamp,
            "dt": datetime.fromtimestamp(result.timestamp).isoformat(),
            "inference_ms": round(result.inference_ms, 1),
            "frame_shape": list(result.frame_shape),
            "detections": [
                {
                    "label": label,
                    "conf": round(box[4], 3),
                    "box": [round(v, 1) for v in box[:4]],
                }
                for box, label in zip(result.boxes, result.labels)
            ],
        }

        if actions:
            entry["actions"] = actions

        self._write_line(json.dumps(entry, ensure_ascii=False))

    def get_stats(self) -> dict:
        """현재 세션 통계"""
        elapsed = time.time() - self._session_start
        return {
            "session_duration_s": round(elapsed, 1),
            "total_frames": self._total_frames,
            "total_detections": self._total_detections,
            "total_actions": self._total_actions,
            "avg_detections_per_frame": (
                round(self._total_detections / self._total_frames, 2)
                if self._total_frames > 0 else 0
            ),
        }

    def reset_stats(self):
        """통계 리셋"""
        self._session_start = time.time()
        self._total_detections = 0
        self._total_actions = 0
        self._total_frames = 0

    def close(self):
        """로거 종료"""
        self._close_file()

    def _write_line(self, line: str):
        """로그 파일에 한 줄 기록 (일별 파일 자동 회전)"""
        today = datetime.now().strftime("%Y-%m-%d")

        if today != self._current_date:
            self._close_file()
            self._current_date = today

        if self._file is None:
            try:
                os.makedirs(self._log_dir, exist_ok=True)
                filepath = os.path.join(self._log_dir, f"vision_{today}.jsonl")
                self._file = open(filepath, "a", encoding="utf-8")
            except Exception as e:
                print(f"[Vision] Log file open error: {e}")
                return

        try:
            self._file.write(line + "\n")
            self._file.flush()
        except Exception as e:
            print(f"[Vision] Log write error: {e}")

    def _close_file(self):
        """현재 로그 파일 닫기"""
        if self._file:
            try:
                self._file.close()
            except Exception:
                pass
            self._file = None
