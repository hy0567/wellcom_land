"""
Multi-KVM Device Manager
"""

import threading
import time
from typing import Dict, List, Optional, Callable
from concurrent.futures import ThreadPoolExecutor, as_completed

from .kvm_device import KVMDevice, KVMInfo, DeviceStatus
from .database import Database


class KVMManager:
    """Manager for multiple KVM devices"""

    def __init__(self, max_workers: int = 10):
        self.db = Database()
        self.devices: Dict[str, KVMDevice] = {}
        self._base_workers = max_workers
        self._monitor_thread: Optional[threading.Thread] = None
        self._monitor_running = False
        self._status_callbacks: List[Callable] = []
        self._lock = threading.RLock()  # RLock: 동일 스레드에서 중첩 lock 허용

    @property
    def max_workers(self) -> int:
        """장치 수에 따라 워커 수 동적 계산 (10~30)"""
        with self._lock:
            device_count = len(self.devices)
        if device_count <= 20:
            return self._base_workers
        elif device_count <= 50:
            return min(20, self._base_workers * 2)
        else:
            return min(30, self._base_workers * 3)

    def load_devices_from_db(self):
        """Load all devices from database (기존 목록 초기화 후 재로드)"""
        device_records = self.db.get_all_devices()

        with self._lock:
            # 기존 연결 해제 후 목록 초기화
            for device in list(self.devices.values()):
                try:
                    device.disconnect()
                except Exception as e:
                    print(f"[KVMManager] 기기 연결 해제 실패 ({device.name}): {e}")
            self.devices.clear()

            for record in device_records:
                group = record.get('group_name') or 'default'
                info = KVMInfo(
                    name=record['name'],
                    ip=record['ip'],
                    port=record['port'],
                    web_port=record['web_port'],
                    username=record['username'],
                    password=record['password'],
                    group=group
                )
                device = KVMDevice(info)
                # 로컬 DB에 저장된 MAC 주소 복원
                if record.get('mac_address'):
                    device.mac_address = record['mac_address']
                self.devices[record['name']] = device

        print(f"Loaded {len(self.devices)} devices from database")

    def load_devices_from_server(self, device_list: list):
        """서버 API에서 받은 기기 목록으로 로드 + 로컬 DB 동기화

        v1.10.44: DB를 서버 기준으로 완전 동기화.
        - 서버에 있는 장치: DB에 추가/업데이트
        - 서버에 없는 DB 장치: DB에서 삭제 (찌꺼기 정리)
        """
        server_names = set()
        with self._lock:
            self.devices.clear()
            for record in device_list:
                name = record.get('name', '')
                if not name:
                    continue
                info = KVMInfo(
                    name=name,
                    ip=record.get('ip', ''),
                    port=record.get('port', 22),
                    web_port=record.get('web_port', 80),
                    username=record.get('username', 'root'),
                    password=record.get('password', 'luckfox'),
                    group=record.get('group_name') or 'default',
                )
                self.devices[name] = KVMDevice(info)
                server_names.add(name)
                # 로컬 DB에도 동기화 (있으면 업데이트, 없으면 추가)
                try:
                    existing = self.db.get_device_by_name(name)
                    if existing:
                        # IP/포트 변경 시 업데이트
                        if existing['ip'] != info.ip or existing.get('web_port') != info.web_port:
                            self.db.update_device(existing['id'],
                                                  ip=info.ip, port=info.port,
                                                  web_port=info.web_port,
                                                  username=info.username,
                                                  password=info.password,
                                                  group_name=info.group)
                    else:
                        self.db.add_device(
                            info.name, info.ip, info.port, info.web_port,
                            info.username, info.password, info.group
                        )
                except Exception as e:
                    print(f"[KVMManager] 로컬 DB 동기화 실패 ({name}): {e}")

        # DB 찌꺼기 정리: 서버에 없는 장치를 로컬 DB에서 삭제
        self._cleanup_db_orphans(server_names)
        print(f"Loaded {len(self.devices)} devices from server")

    def merge_devices_from_server(self, device_list: list):
        """서버 기기를 기존 목록에 병합 (로컬 기기 유지)

        v1.10.43: 이름뿐 아니라 IP 기준으로도 중복 체크.
        v1.10.44: DB 찌꺼기 정리 + 메모리 중복 제거 강화.
        이름 변경 후 재시작 시 서버의 옛 이름 장치가 중복 추가되는 문제 방지.
        """
        added = 0
        skipped_name = 0
        skipped_ip = 0

        # 서버 + 로컬 합산된 유효 이름 수집 (DB 정리용)
        valid_names = set()

        with self._lock:
            # 기존 장치의 IP 목록 (빠른 조회용)
            existing_ips = {dev.ip for dev in self.devices.values()}

            # 로컬 DB에서 로드된 장치는 유효
            valid_names.update(self.devices.keys())

            for record in device_list:
                name = record.get('name', '')
                ip = record.get('ip', '')
                if not name:
                    continue

                # 이름 중복 체크
                if name in self.devices:
                    skipped_name += 1
                    valid_names.add(name)
                    continue

                # IP 중복 체크 — 같은 IP가 이미 로컬에 있으면 스킵
                # (이름 변경 후 서버에 옛 이름이 남아있는 경우)
                if ip and ip in existing_ips:
                    # 로컬에 이미 같은 IP 장치가 다른 이름으로 존재
                    local_name = next(
                        (n for n, d in self.devices.items() if d.ip == ip), None
                    )
                    print(f"[KVMManager] 서버 병합 스킵: {name} ({ip}) "
                          f"— 같은 IP가 '{local_name}'으로 이미 존재")
                    skipped_ip += 1
                    continue

                info = KVMInfo(
                    name=name,
                    ip=ip,
                    port=record.get('port', 22),
                    web_port=record.get('web_port', 80),
                    username=record.get('username', 'root'),
                    password=record.get('password', 'luckfox'),
                    group=record.get('group_name') or 'default',
                )
                self.devices[name] = KVMDevice(info)
                existing_ips.add(ip)  # 새로 추가된 IP도 추적
                valid_names.add(name)
                # 로컬 DB에도 저장
                try:
                    existing = self.db.get_device_by_name(name)
                    if existing:
                        # IP 변경 시 업데이트
                        if existing['ip'] != ip:
                            self.db.update_device(existing['id'], ip=ip)
                    else:
                        # IP로도 확인 — DB에 같은 IP가 다른 이름으로 있으면 추가하지 않음
                        existing_by_ip = self.db.get_device_by_ip(ip) if ip else None
                        if not existing_by_ip:
                            self.db.add_device(
                                info.name, info.ip, info.port, info.web_port,
                                info.username, info.password, info.group
                            )
                except Exception as e:
                    print(f"[KVMManager] 로컬 DB 병합 저장 실패 ({name}): {e}")
                added += 1

        # DB 찌꺼기 정리: 유효 목록에 없는 DB 레코드 삭제
        self._cleanup_db_orphans(valid_names)

        skip_info = ""
        if skipped_ip > 0:
            skip_info = f", IP 중복 스킵: {skipped_ip}개"
        print(f"Merged {added} new devices from server (total: {len(self.devices)}{skip_info})")

    def _cleanup_db_orphans(self, valid_names: set):
        """DB에서 유효 목록에 없는 장치(찌꺼기) 삭제

        v1.10.44: 이름 변경/삭제 후 DB에 남아있는 옛 레코드 정리.
        """
        try:
            db_devices = self.db.get_all_devices()
            removed = 0
            for record in db_devices:
                db_name = record['name']
                if db_name not in valid_names:
                    self.db.delete_device(record['id'])
                    removed += 1
                    print(f"[KVMManager] DB 찌꺼기 삭제: {db_name} ({record.get('ip', '?')})")
            if removed:
                print(f"[KVMManager] DB 찌꺼기 {removed}개 정리 완료")
        except Exception as e:
            print(f"[KVMManager] DB 찌꺼기 정리 오류: {e}")

    def add_device(self, name: str, ip: str, port: int = 22, web_port: int = 80,
                   username: str = "root", password: str = "luckfox",
                   group: str = "default") -> KVMDevice:
        """Add new KVM device (로컬 DB + 서버 동기화)"""
        # Add to local database
        self.db.add_device(name, ip, port, web_port, username, password, group)

        # Create device instance
        info = KVMInfo(name, ip, port, web_port, username, password, group)
        device = KVMDevice(info)

        with self._lock:
            self.devices[name] = device

        # 서버에도 동기화 (admin인 경우)
        self._sync_device_to_server(name, ip, port, web_port, username, password)

        return device

    def _sync_device_to_server(self, name: str, ip: str, port: int = 22,
                                web_port: int = 80, username: str = "root",
                                password: str = "luckfox"):
        """단일 기기를 서버에 동기화 (실패해도 무시)"""
        try:
            from api_client import api_client
            if api_client.is_logged_in and api_client.is_admin:
                api_client.sync_device_to_server({
                    'name': name,
                    'ip': ip,
                    'port': port,
                    'web_port': web_port,
                    'username': username,
                    'password': password,
                })
                print(f"[KVMManager] 서버 동기화 완료: {name} ({ip})")
        except Exception as e:
            print(f"[KVMManager] 서버 동기화 실패 (무시): {e}")

    def sync_all_to_server(self) -> dict:
        """로컬 DB의 모든 기기를 서버에 일괄 동기화
        Returns: {'synced': int, 'skipped': int, 'failed': int}
        """
        try:
            from api_client import api_client
            if not api_client.is_logged_in or not api_client.is_admin:
                return {'synced': 0, 'skipped': 0, 'failed': 0, 'error': '관리자 로그인 필요'}

            device_list = []
            with self._lock:
                for device in list(self.devices.values()):
                    device_list.append({
                        'name': device.name,
                        'ip': device.ip,
                        'port': device.info.port,
                        'web_port': device.info.web_port,
                        'username': device.info.username,
                        'password': device.info.password,
                    })

            result = api_client.sync_devices_to_server(device_list)
            print(f"[KVMManager] 일괄 동기화 결과: {result}")
            return result
        except Exception as e:
            print(f"[KVMManager] 일괄 동기화 실패: {e}")
            return {'synced': 0, 'skipped': 0, 'failed': 0, 'error': str(e)}

    def rename_device(self, old_name: str, new_name: str) -> bool:
        """장치 이름 변경 (로컬 DB + 메모리 + 서버 동기화)
        Returns: True if success
        """
        if old_name == new_name:
            return True
        with self._lock:
            if new_name in self.devices:
                return False  # 이름 중복

        with self._lock:
            device = self.devices.get(old_name)
            if not device:
                return False

            # 1) 로컬 DB 업데이트
            record = self.db.get_device_by_name(old_name)
            if record:
                self.db.update_device(record['id'], name=new_name)

            # 2) 메모리 업데이트
            device.info.name = new_name
            del self.devices[old_name]
            self.devices[new_name] = device

        # 3) 서버 동기화 (admin인 경우)
        try:
            from api_client import api_client
            if api_client.is_logged_in and api_client.is_admin:
                try:
                    server_devices = api_client.admin_get_all_devices()
                    for sd in server_devices:
                        if sd.get('ip') == device.ip or sd.get('name') == old_name:
                            api_client.admin_update_device(sd['id'], {'name': new_name})
                            print(f"[KVMManager] 서버 이름 변경: {old_name} → {new_name}")
                            break
                except Exception as e:
                    print(f"[KVMManager] 서버 이름 변경 실패 (무시): {e}")
        except Exception as e:
            print(f"[KVMManager] api_client import 실패: {e}")

        print(f"[KVMManager] 이름 변경: {old_name} → {new_name}")
        return True

    def remove_device(self, name: str):
        """Remove KVM device"""
        with self._lock:
            if name in self.devices:
                self.devices[name].disconnect()
                del self.devices[name]

        # Remove from database
        record = self.db.get_device_by_name(name)
        if record:
            self.db.delete_device(record['id'])

    def get_device(self, name: str) -> Optional[KVMDevice]:
        """Get device by name"""
        with self._lock:
            return self.devices.get(name)

    def get_device_by_ip(self, ip: str) -> Optional[KVMDevice]:
        """Get device by IP"""
        with self._lock:
            for device in list(self.devices.values()):
                if device.ip == ip:
                    return device
        return None

    def get_all_devices(self) -> List[KVMDevice]:
        """Get all devices"""
        with self._lock:
            return list(self.devices.values())

    def get_devices_by_group(self, group: str) -> List[KVMDevice]:
        """Get devices by group"""
        with self._lock:
            return [d for d in self.devices.values() if d.info.group == group]

    def get_online_devices(self) -> List[KVMDevice]:
        """Get online devices"""
        with self._lock:
            return [d for d in self.devices.values() if d.status == DeviceStatus.ONLINE]

    def get_offline_devices(self) -> List[KVMDevice]:
        """Get offline devices"""
        with self._lock:
            return [d for d in self.devices.values() if d.status == DeviceStatus.OFFLINE]

    # ==================== Batch Operations ====================

    def connect_all(self, parallel: bool = True) -> Dict[str, bool]:
        """Connect to all devices"""
        results = {}

        with self._lock:
            devices_snapshot = list(self.devices.values())

        if parallel:
            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                futures = {executor.submit(d.connect): d.name for d in devices_snapshot}
                for future in as_completed(futures):
                    name = futures[future]
                    try:
                        results[name] = future.result()
                    except Exception as e:
                        results[name] = False
                        print(f"[{name}] Connection error: {e}")
        else:
            for device in devices_snapshot:
                results[device.name] = device.connect()

        return results

    def disconnect_all(self):
        """Disconnect all devices"""
        with self._lock:
            devices_snapshot = list(self.devices.values())
        for device in devices_snapshot:
            device.disconnect()

    def refresh_status_all(self) -> Dict[str, dict]:
        """Refresh status of all devices"""
        results = {}

        with self._lock:
            devices_snapshot = list(self.devices.values())

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {executor.submit(self._get_device_status, d): d.name for d in devices_snapshot}
            for future in as_completed(futures):
                name = futures[future]
                try:
                    results[name] = future.result()
                except Exception as e:
                    results[name] = {'error': str(e)}

        return results

    def _get_device_status(self, device: KVMDevice) -> dict:
        """Get single device status"""
        if not device.is_connected():
            device.connect()

        if device.is_connected():
            return device.get_system_info()
        else:
            return {'status': 'offline'}

    def execute_on_all(self, func: Callable, *args, **kwargs) -> Dict[str, any]:
        """Execute function on all devices"""
        results = {}

        with self._lock:
            devices_snapshot = list(self.devices.values())

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {executor.submit(func, d, *args, **kwargs): d.name for d in devices_snapshot}
            for future in as_completed(futures):
                name = futures[future]
                try:
                    results[name] = future.result()
                except Exception as e:
                    results[name] = {'error': str(e)}

        return results

    def execute_on_group(self, group: str, func: Callable, *args, **kwargs) -> Dict[str, any]:
        """Execute function on devices in group"""
        devices = self.get_devices_by_group(group)
        results = {}

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {executor.submit(func, d, *args, **kwargs): d.name for d in devices}
            for future in as_completed(futures):
                name = futures[future]
                try:
                    results[name] = future.result()
                except Exception as e:
                    results[name] = {'error': str(e)}

        return results

    # ==================== Monitoring ====================

    def add_status_callback(self, callback: Callable):
        """Add status change callback"""
        self._status_callbacks.append(callback)

    def remove_status_callback(self, callback: Callable):
        """Remove status change callback"""
        if callback in self._status_callbacks:
            self._status_callbacks.remove(callback)

    def start_monitoring(self, interval: float = 5.0):
        """Start background monitoring thread"""
        if self._monitor_running:
            return

        self._monitor_running = True
        self._monitor_thread = threading.Thread(target=self._monitor_loop, args=(interval,), daemon=True)
        self._monitor_thread.start()

    def stop_monitoring(self):
        """Stop monitoring thread"""
        self._monitor_running = False
        if self._monitor_thread:
            self._monitor_thread.join(timeout=5)
            self._monitor_thread = None

    def _monitor_loop(self, interval: float):
        """Monitoring loop"""
        while self._monitor_running:
            try:
                status_updates = self.refresh_status_all()

                # Notify callbacks
                for callback in self._status_callbacks:
                    try:
                        callback(status_updates)
                    except Exception as e:
                        print(f"Callback error: {e}")

            except Exception as e:
                print(f"Monitor error: {e}")

            time.sleep(interval)

    # ==================== Group Management ====================

    def add_group(self, name: str, description: str = ""):
        """Add new group"""
        self.db.add_group(name, description)

    def delete_group(self, name: str):
        """Delete group"""
        self.db.delete_group(name)

        # Update device instances
        with self._lock:
            for device in list(self.devices.values()):
                if device.info.group == name:
                    device.info.group = "default"

    def get_groups(self) -> List[dict]:
        """Get all groups"""
        return self.db.get_all_groups()

    def move_device_to_group(self, device_name: str, group_name: str):
        """Move device to group"""
        record = self.db.get_device_by_name(device_name)
        if record:
            self.db.update_device(record['id'], group_name=group_name)

        with self._lock:
            if device_name in self.devices:
                self.devices[device_name].info.group = group_name

    # ==================== Statistics ====================

    def get_statistics(self) -> dict:
        """Get overall statistics"""
        with self._lock:
            devices_snapshot = list(self.devices.values())

        total = len(devices_snapshot)
        online = len([d for d in devices_snapshot if d.status == DeviceStatus.ONLINE])
        offline = total - online

        groups = {}
        for device in devices_snapshot:
            group = device.info.group
            if group not in groups:
                groups[group] = {'total': 0, 'online': 0}
            groups[group]['total'] += 1
            if device.status == DeviceStatus.ONLINE:
                groups[group]['online'] += 1

        return {
            'total': total,
            'online': online,
            'offline': offline,
            'groups': groups
        }
