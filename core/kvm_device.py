"""
Single KVM Device Controller
"""

import paramiko
import requests
import struct
import time
import threading
from typing import Optional, Callable
from dataclasses import dataclass
from enum import Enum


class DeviceStatus(Enum):
    ONLINE = "online"
    OFFLINE = "offline"
    ERROR = "error"
    UNKNOWN = "unknown"


class USBStatus(Enum):
    CONFIGURED = "configured"
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    NOT_ATTACHED = "not_attached"


@dataclass
class KVMInfo:
    name: str
    ip: str
    port: int = 22
    web_port: int = 80
    username: str = "root"
    password: str = "luckfox"
    group: str = "default"


class KVMDevice:
    """Single PicoKVM Device Controller"""

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

    # Modifier Keys
    MOD_LCTRL = 0x01
    MOD_LSHIFT = 0x02
    MOD_LALT = 0x04
    MOD_LMETA = 0x08
    MOD_RCTRL = 0x10
    MOD_RSHIFT = 0x20
    MOD_RALT = 0x40
    MOD_RMETA = 0x80

    def __init__(self, info: KVMInfo):
        self.info = info
        self.ssh: Optional[paramiko.SSHClient] = None
        self.status = DeviceStatus.UNKNOWN
        self.usb_status = USBStatus.DISCONNECTED
        self.version = ""
        self.system_version = ""
        self._lock = threading.Lock()
        self._connected = False

    @property
    def name(self) -> str:
        return self.info.name

    @property
    def ip(self) -> str:
        return self.info.ip

    def connect(self) -> bool:
        """Connect to KVM via SSH"""
        try:
            with self._lock:
                if self._connected and self.ssh:
                    return True

                self.ssh = paramiko.SSHClient()
                self.ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                self.ssh.connect(
                    self.info.ip,
                    port=self.info.port,
                    username=self.info.username,
                    password=self.info.password,
                    timeout=10
                )
                self._connected = True
                self.status = DeviceStatus.ONLINE
                self._update_device_info()
                return True
        except Exception as e:
            self.status = DeviceStatus.OFFLINE
            self._connected = False
            print(f"[{self.name}] Connection failed: {e}")
            return False

    def disconnect(self):
        """Disconnect SSH"""
        with self._lock:
            if self.ssh:
                try:
                    self.ssh.close()
                except:
                    pass
                self.ssh = None
            self._connected = False
            self.status = DeviceStatus.OFFLINE

    def is_connected(self) -> bool:
        """Check connection status"""
        return self._connected and self.ssh is not None

    def _exec_command(self, cmd: str, timeout: int = 10) -> tuple:
        """Execute SSH command (thread-safe with lock)"""
        with self._lock:
            if not self.is_connected():
                if not self.connect():
                    return "", "Not connected"

            try:
                stdin, stdout, stderr = self.ssh.exec_command(cmd, timeout=timeout)
                return stdout.read().decode().strip(), stderr.read().decode().strip()
            except Exception as e:
                self._connected = False
                return "", str(e)

    def _update_device_info(self):
        """Update device version info"""
        out, _ = self._exec_command("cat /version")
        self.system_version = out

        # Get app version from web API
        try:
            resp = requests.get(f"http://{self.info.ip}:{self.info.web_port}/api/version", timeout=5)
            if resp.ok:
                data = resp.json()
                self.version = data.get("app", "")
        except:
            pass

    def get_usb_status(self) -> USBStatus:
        """Get USB connection status"""
        out, _ = self._exec_command("cat /sys/class/udc/*/state 2>/dev/null")

        if "configured" in out:
            self.usb_status = USBStatus.CONFIGURED
        elif "connected" in out:
            self.usb_status = USBStatus.CONNECTED
        elif "not attached" in out or not out:
            self.usb_status = USBStatus.NOT_ATTACHED
        else:
            self.usb_status = USBStatus.DISCONNECTED

        return self.usb_status

    def get_system_info(self) -> dict:
        """Get system information"""
        info = {}

        # Uptime
        out, _ = self._exec_command("uptime")
        info['uptime'] = out

        # Memory
        out, _ = self._exec_command("free -m | grep Mem")
        if out:
            parts = out.split()
            if len(parts) >= 3:
                info['memory_total'] = int(parts[1])
                info['memory_used'] = int(parts[2])

        # Temperature
        out, _ = self._exec_command("cat /sys/class/thermal/thermal_zone*/temp 2>/dev/null")
        if out:
            try:
                info['temperature'] = int(out) / 1000
            except:
                pass

        # USB Status
        info['usb_status'] = self.get_usb_status().value

        return info

    def get_dmesg_usb(self, lines: int = 20) -> str:
        """Get USB related dmesg logs"""
        out, _ = self._exec_command(f"dmesg | grep -i 'usb\\|disconnect' | tail -{lines}")
        return out

    # ==================== HID Control ====================

    def _bytes_to_escape(self, data: bytes) -> str:
        """Convert bytes to shell escape sequence"""
        return ''.join(f'\\x{b:02x}' for b in data)

    def send_key(self, key: str, modifiers: int = 0, duration: float = 0.05):
        """Send keyboard key press"""
        key_code = self.KEY_CODES.get(key.lower(), 0)
        if not key_code:
            return False

        # Key press
        report = struct.pack('BBBBBBBB', modifiers, 0, key_code, 0, 0, 0, 0, 0)
        cmd = f"echo -ne '{self._bytes_to_escape(report)}' > /dev/hidg0"
        self._exec_command(cmd)

        time.sleep(duration)

        # Key release
        release = struct.pack('BBBBBBBB', 0, 0, 0, 0, 0, 0, 0, 0)
        cmd = f"echo -ne '{self._bytes_to_escape(release)}' > /dev/hidg0"
        self._exec_command(cmd)

        return True

    def send_text(self, text: str, delay: float = 0.05):
        """Send text string"""
        for char in text:
            if char.isupper():
                self.send_key(char.lower(), self.MOD_LSHIFT, delay)
            elif char == ' ':
                self.send_key('space', 0, delay)
            elif char == '\n':
                self.send_key('enter', 0, delay)
            else:
                self.send_key(char, 0, delay)
            time.sleep(delay)

    def send_mouse_relative(self, dx: int, dy: int, buttons: int = 0):
        """Send relative mouse movement"""
        # Clamp values to signed byte range
        dx = max(-127, min(127, dx))
        dy = max(-127, min(127, dy))

        report = struct.pack('Bbb', buttons, dx, dy)
        cmd = f"echo -ne '{self._bytes_to_escape(report)}' > /dev/hidg2"
        self._exec_command(cmd)

    def send_mouse_absolute(self, x: int, y: int, buttons: int = 0):
        """Send absolute mouse position (0-32767)"""
        x = max(0, min(32767, x))
        y = max(0, min(32767, y))

        report = struct.pack('<BHH', buttons, x, y)
        cmd = f"echo -ne '{self._bytes_to_escape(report)}' > /dev/hidg1"
        self._exec_command(cmd)

    def mouse_click(self, button: str = 'left'):
        """Send mouse click"""
        btn_map = {'left': 1, 'right': 2, 'middle': 4}
        btn = btn_map.get(button, 1)

        self.send_mouse_relative(0, 0, btn)
        time.sleep(0.05)
        self.send_mouse_relative(0, 0, 0)

    # ==================== Device Control ====================

    def reboot(self):
        """Reboot KVM device"""
        self._exec_command("reboot")
        self._connected = False
        self.status = DeviceStatus.OFFLINE

    def reconnect_usb(self):
        """Reconnect USB gadget"""
        self._exec_command("echo '' > /sys/kernel/config/usb_gadget/kvm/UDC 2>/dev/null")
        time.sleep(1)
        self._exec_command("echo 'ffb00000.usb' > /sys/kernel/config/usb_gadget/kvm/UDC 2>/dev/null")

    def set_mouse_mode(self, absolute: bool = True, relative: bool = True):
        """Set mouse mode"""
        abs_val = "true" if absolute else "false"
        rel_val = "true" if relative else "false"

        self._exec_command(f"sed -i 's/\"absolute_mouse\": [a-z]*/\"absolute_mouse\": {abs_val}/' /userdata/kvm_config.json")
        self._exec_command(f"sed -i 's/\"relative_mouse\": [a-z]*/\"relative_mouse\": {rel_val}/' /userdata/kvm_config.json")

    def set_video_quality(self, quality: int) -> bool:
        """
        Set video stream quality (10-100%)

        Luckfox PicoKVM video quality control via IPC socket:
        - /var/run/kvm_ctrl.sock 을 통해 JSON-RPC 전송
        - setStreamQualityFactor 메서드 사용

        Args:
            quality: Quality percentage (10-100)

        Returns:
            bool: Success status
        """
        quality = max(10, min(100, quality))

        try:
            # IPC 소켓을 통해 setStreamQualityFactor RPC 전송
            # socat을 사용하여 Unix 도메인 소켓에 JSON-RPC 전송
            rpc_msg = f'{{"jsonrpc":"2.0","id":1,"method":"setStreamQualityFactor","params":{{"factor":{quality}}}}}'

            cmd = f'printf "%s\\n" \'{rpc_msg}\' | socat -t1 - UNIX-CLIENT:/var/run/kvm_ctrl.sock 2>/dev/null'
            out, err = self._exec_command(cmd)

            print(f"[{self.name}] Video quality set to {quality}%")
            return True

        except Exception as e:
            print(f"[{self.name}] Failed to set video quality: {e}")
            return False

    def get_video_quality(self) -> int:
        """Get current video quality setting"""
        config = self.get_config()
        if 'video' in config:
            return config['video'].get('quality', 80)
        return 80  # Default

    def get_config(self) -> dict:
        """Get KVM configuration"""
        out, _ = self._exec_command("cat /userdata/kvm_config.json")
        try:
            import json
            return json.loads(out)
        except:
            return {}

    def __repr__(self):
        return f"KVMDevice({self.name}, {self.ip}, {self.status.value})"
