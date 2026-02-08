"""
고속 HID 컨트롤러 - 지속적인 SSH 채널 사용
"""

import paramiko
import struct
import threading
import queue
import time
from typing import Optional


class FastHIDController:
    """SSH 채널을 유지하여 빠른 HID 명령 전송"""

    # HID Key Codes
    KEY_CODES = {
        'a': 0x04, 'b': 0x05, 'c': 0x06, 'd': 0x07, 'e': 0x08, 'f': 0x09,
        'g': 0x0A, 'h': 0x0B, 'i': 0x0C, 'j': 0x0D, 'k': 0x0E, 'l': 0x0F,
        'm': 0x10, 'n': 0x11, 'o': 0x12, 'p': 0x13, 'q': 0x14, 'r': 0x15,
        's': 0x16, 't': 0x17, 'u': 0x18, 'v': 0x19, 'w': 0x1A, 'x': 0x1B,
        'y': 0x1C, 'z': 0x1D, '1': 0x1E, '2': 0x1F, '3': 0x20, '4': 0x21,
        '5': 0x22, '6': 0x23, '7': 0x24, '8': 0x25, '9': 0x26, '0': 0x27,
        'enter': 0x28, 'esc': 0x29, 'backspace': 0x2A, 'tab': 0x2B,
        'space': 0x2C, 'f1': 0x3A, 'f2': 0x3B, 'f3': 0x3C, 'f4': 0x3D,
        'f5': 0x3E, 'f6': 0x3F, 'f7': 0x40, 'f8': 0x41, 'f9': 0x42,
        'f10': 0x43, 'f11': 0x44, 'f12': 0x45,
        'up': 0x52, 'down': 0x51, 'left': 0x50, 'right': 0x4F,
    }

    def __init__(self, ip: str, port: int = 22, username: str = "root", password: str = "luckfox"):
        self.ip = ip
        self.port = port
        self.username = username
        self.password = password

        self.ssh: Optional[paramiko.SSHClient] = None
        self.shell: Optional[paramiko.Channel] = None
        self._connected = False
        self._lock = threading.Lock()

        # 명령 큐 (비동기 전송용)
        self._cmd_queue = queue.Queue()
        self._worker_thread: Optional[threading.Thread] = None
        self._running = False

    def connect(self) -> bool:
        """SSH 연결 및 쉘 채널 열기"""
        try:
            with self._lock:
                if self._connected:
                    return True

                self.ssh = paramiko.SSHClient()
                self.ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                self.ssh.connect(
                    self.ip,
                    port=self.port,
                    username=self.username,
                    password=self.password,
                    timeout=5
                )

                # 인터랙티브 쉘 열기
                self.shell = self.ssh.invoke_shell()
                self.shell.settimeout(0.1)

                # 초기 프롬프트 읽기
                time.sleep(0.1)
                while self.shell.recv_ready():
                    self.shell.recv(4096)

                self._connected = True

                # 워커 스레드 시작
                self._running = True
                self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
                self._worker_thread.start()

                return True

        except Exception as e:
            print(f"[HID] Connection failed: {e}")
            self._connected = False
            return False

    def disconnect(self):
        """연결 종료"""
        self._running = False
        if self._worker_thread:
            self._worker_thread.join(timeout=1)

        with self._lock:
            if self.shell:
                self.shell.close()
                self.shell = None
            if self.ssh:
                self.ssh.close()
                self.ssh = None
            self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    def _worker_loop(self):
        """명령 큐 처리 워커"""
        while self._running:
            try:
                cmd = self._cmd_queue.get(timeout=0.01)
                self._send_raw(cmd)
            except queue.Empty:
                pass
            except Exception as e:
                print(f"[HID] Worker error: {e}")

    def _send_raw(self, cmd: str):
        """쉘을 통해 명령 전송"""
        if not self._connected or not self.shell:
            return

        try:
            self.shell.send(cmd + "\n")
            # 응답 읽기 (비차단)
            time.sleep(0.001)
            while self.shell.recv_ready():
                self.shell.recv(1024)
        except Exception as e:
            print(f"[HID] Send error: {e}")
            self._connected = False

    def _bytes_to_hex(self, data: bytes) -> str:
        """바이트를 16진수 문자열로 변환"""
        return ''.join(f'\\x{b:02x}' for b in data)

    def send_mouse_relative(self, dx: int, dy: int, buttons: int = 0):
        """상대 마우스 이동 (큐에 추가)"""
        dx = max(-127, min(127, dx))
        dy = max(-127, min(127, dy))

        report = struct.pack('Bbb', buttons, dx, dy)
        cmd = f"echo -ne '{self._bytes_to_hex(report)}' > /dev/hidg2"
        self._cmd_queue.put(cmd)

    def send_mouse_click(self, button: str = 'left'):
        """마우스 클릭"""
        btn_map = {'left': 1, 'right': 2, 'middle': 4}
        btn = btn_map.get(button, 1)

        # 버튼 누름
        report_down = struct.pack('Bbb', btn, 0, 0)
        cmd_down = f"echo -ne '{self._bytes_to_hex(report_down)}' > /dev/hidg2"
        self._cmd_queue.put(cmd_down)

        # 버튼 놓음
        report_up = struct.pack('Bbb', 0, 0, 0)
        cmd_up = f"echo -ne '{self._bytes_to_hex(report_up)}' > /dev/hidg2"
        self._cmd_queue.put(cmd_up)

    def send_key(self, key: str, modifiers: int = 0):
        """키 입력"""
        key_code = self.KEY_CODES.get(key.lower(), 0)
        if not key_code:
            return

        # 키 누름
        report_down = struct.pack('BBBBBBBB', modifiers, 0, key_code, 0, 0, 0, 0, 0)
        cmd_down = f"echo -ne '{self._bytes_to_hex(report_down)}' > /dev/hidg0"
        self._cmd_queue.put(cmd_down)

        # 키 놓음
        report_up = struct.pack('BBBBBBBB', 0, 0, 0, 0, 0, 0, 0, 0)
        cmd_up = f"echo -ne '{self._bytes_to_hex(report_up)}' > /dev/hidg0"
        self._cmd_queue.put(cmd_up)

    def send_hid_code(self, hid_code: int, modifiers: int = 0):
        """HID 키코드 직접 전송 (KEY_CODES 매핑 없이)"""
        # 키 누름
        report_down = struct.pack('BBBBBBBB', modifiers, 0, hid_code, 0, 0, 0, 0, 0)
        cmd_down = f"echo -ne '{self._bytes_to_hex(report_down)}' > /dev/hidg0"
        self._cmd_queue.put(cmd_down)

        # 키 놓음
        report_up = struct.pack('BBBBBBBB', 0, 0, 0, 0, 0, 0, 0, 0)
        cmd_up = f"echo -ne '{self._bytes_to_hex(report_up)}' > /dev/hidg0"
        self._cmd_queue.put(cmd_up)

    def flush(self):
        """큐 비우기"""
        while not self._cmd_queue.empty():
            try:
                self._cmd_queue.get_nowait()
            except:
                break
