"""
감지 결과 → HID 액션 디스패처
규칙 기반으로 감지된 객체에 대해 키보드/마우스 자동 입력 또는 알림 생성
"""

from dataclasses import dataclass, field
from typing import Optional, Callable

from .detector import DetectionResult


@dataclass
class ActionRule:
    """단일 액션 규칙"""
    class_name: str             # 감지 클래스명 (예: "enemy", "hp_bar")
    action_type: str            # "key", "mouse_click", "mouse_move", "alert"
    params: dict = field(default_factory=dict)   # {"key": "f1"} / {"button": "left"} 등
    min_confidence: float = 0.5
    cooldown_ms: float = 1000   # 동일 규칙 재실행 대기 시간 (ms)
    region: Optional[list] = None  # [x1_pct, y1_pct, x2_pct, y2_pct] 화면 영역 필터 (0~1)

    def to_dict(self) -> dict:
        return {
            'class_name': self.class_name,
            'action_type': self.action_type,
            'params': self.params,
            'min_confidence': self.min_confidence,
            'cooldown_ms': self.cooldown_ms,
            'region': self.region,
        }

    @staticmethod
    def from_dict(d: dict) -> 'ActionRule':
        return ActionRule(
            class_name=d.get('class_name', ''),
            action_type=d.get('action_type', 'alert'),
            params=d.get('params', {}),
            min_confidence=d.get('min_confidence', 0.5),
            cooldown_ms=d.get('cooldown_ms', 1000),
            region=d.get('region'),
        )


class ActionDispatcher:
    """감지 결과를 규칙에 따라 HID 액션으로 변환"""

    def __init__(self, hid_controller=None):
        self._hid = hid_controller
        self._rules: list[ActionRule] = []
        self._enabled = False
        self._last_trigger: dict[str, float] = {}  # rule_key → last trigger timestamp
        self._alert_callback: Optional[Callable] = None

    def set_hid_controller(self, hid_controller):
        """HID 컨트롤러 설정/변경"""
        self._hid = hid_controller

    def load_rules(self, rules: list[ActionRule]):
        """규칙 목록 로드"""
        self._rules = rules
        self._last_trigger.clear()

    def load_rules_from_dicts(self, rule_dicts: list[dict]):
        """딕셔너리 목록에서 규칙 로드 (설정 파일용)"""
        self._rules = [ActionRule.from_dict(d) for d in rule_dicts]
        self._last_trigger.clear()

    def set_enabled(self, enabled: bool):
        """자동 액션 활성화/비활성화"""
        self._enabled = enabled

    def set_alert_callback(self, callback: Optional[Callable]):
        """알림 콜백 설정 (alert 액션용)"""
        self._alert_callback = callback

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    @property
    def rules(self) -> list[ActionRule]:
        return self._rules

    def process(self, result: DetectionResult) -> list[dict]:
        """
        감지 결과를 규칙에 따라 처리.
        Returns: 실행된 액션 목록 [{"rule": ..., "action": ..., "detection": ...}, ...]
        """
        if not self._enabled or not self._rules:
            return []

        executed = []
        now = result.timestamp

        for box, label in zip(result.boxes, result.labels):
            x1, y1, x2, y2, conf, cls_id = box

            for rule in self._rules:
                if rule.class_name != label:
                    continue

                if conf < rule.min_confidence:
                    continue

                # 영역 필터
                if rule.region and result.frame_shape[0] > 0:
                    frame_h, frame_w = result.frame_shape
                    cx = (x1 + x2) / 2 / frame_w
                    cy = (y1 + y2) / 2 / frame_h
                    rx1, ry1, rx2, ry2 = rule.region
                    if not (rx1 <= cx <= rx2 and ry1 <= cy <= ry2):
                        continue

                # 쿨다운 체크
                rule_key = f"{rule.class_name}:{rule.action_type}"
                last = self._last_trigger.get(rule_key, 0)
                if (now - last) * 1000 < rule.cooldown_ms:
                    continue

                # 액션 실행
                action_info = self._execute_action(rule, box, result.frame_shape)
                if action_info:
                    self._last_trigger[rule_key] = now
                    executed.append({
                        "rule": rule.to_dict(),
                        "action": action_info,
                        "detection": {
                            "label": label,
                            "confidence": conf,
                            "box": [x1, y1, x2, y2],
                        }
                    })

        return executed

    def _execute_action(self, rule: ActionRule, box: list, frame_shape: tuple) -> Optional[dict]:
        """단일 액션 실행"""
        action_type = rule.action_type
        params = rule.params

        if action_type == "key":
            return self._action_key(params)
        elif action_type == "mouse_click":
            return self._action_mouse_click(params, box, frame_shape)
        elif action_type == "mouse_move":
            return self._action_mouse_move(params, box, frame_shape)
        elif action_type == "alert":
            return self._action_alert(params, rule.class_name, box)

        return None

    def _action_key(self, params: dict) -> Optional[dict]:
        """키보드 입력 액션"""
        key = params.get("key", "")
        modifiers = params.get("modifiers", 0)

        if not key or self._hid is None:
            return None

        self._hid.send_key(key, modifiers)
        return {"type": "key", "key": key, "modifiers": modifiers}

    def _action_mouse_click(self, params: dict, box: list, frame_shape: tuple) -> Optional[dict]:
        """마우스 클릭 액션 (감지된 객체 중심에 클릭)"""
        button = params.get("button", "left")

        if self._hid is None:
            return None

        self._hid.send_mouse_click(button)
        return {"type": "mouse_click", "button": button}

    def _action_mouse_move(self, params: dict, box: list, frame_shape: tuple) -> Optional[dict]:
        """마우스 이동 액션 (감지된 객체 방향으로 이동)"""
        if self._hid is None:
            return None

        x1, y1, x2, y2, conf, cls_id = box
        frame_h, frame_w = frame_shape

        # 객체 중심과 화면 중심 사이의 오프셋 계산
        obj_cx = (x1 + x2) / 2
        obj_cy = (y1 + y2) / 2
        screen_cx = frame_w / 2
        screen_cy = frame_h / 2

        speed = params.get("speed", 5)
        dx = int((obj_cx - screen_cx) / frame_w * speed * 127)
        dy = int((obj_cy - screen_cy) / frame_h * speed * 127)

        dx = max(-127, min(127, dx))
        dy = max(-127, min(127, dy))

        self._hid.send_mouse_relative(dx, dy)
        return {"type": "mouse_move", "dx": dx, "dy": dy}

    def _action_alert(self, params: dict, class_name: str, box: list) -> Optional[dict]:
        """알림 액션 (콜백 호출)"""
        message = params.get("message", f"{class_name} 감지됨")

        if self._alert_callback:
            x1, y1, x2, y2, conf, cls_id = box
            self._alert_callback(message, class_name, conf)

        return {"type": "alert", "message": message}
