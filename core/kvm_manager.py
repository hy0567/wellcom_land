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
        self.max_workers = max_workers
        self._monitor_thread: Optional[threading.Thread] = None
        self._monitor_running = False
        self._status_callbacks: List[Callable] = []
        self._lock = threading.Lock()

    def load_devices_from_db(self):
        """Load all devices from database"""
        device_records = self.db.get_all_devices()

        for record in device_records:
            info = KVMInfo(
                name=record['name'],
                ip=record['ip'],
                port=record['port'],
                web_port=record['web_port'],
                username=record['username'],
                password=record['password'],
                group=record['group_name']
            )
            self.devices[record['name']] = KVMDevice(info)

        print(f"Loaded {len(self.devices)} devices from database")

    def add_device(self, name: str, ip: str, port: int = 22, web_port: int = 80,
                   username: str = "root", password: str = "luckfox",
                   group: str = "default") -> KVMDevice:
        """Add new KVM device"""
        # Add to database
        self.db.add_device(name, ip, port, web_port, username, password, group)

        # Create device instance
        info = KVMInfo(name, ip, port, web_port, username, password, group)
        device = KVMDevice(info)

        with self._lock:
            self.devices[name] = device

        return device

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
        return self.devices.get(name)

    def get_device_by_ip(self, ip: str) -> Optional[KVMDevice]:
        """Get device by IP"""
        for device in self.devices.values():
            if device.ip == ip:
                return device
        return None

    def get_all_devices(self) -> List[KVMDevice]:
        """Get all devices"""
        return list(self.devices.values())

    def get_devices_by_group(self, group: str) -> List[KVMDevice]:
        """Get devices by group"""
        return [d for d in self.devices.values() if d.info.group == group]

    def get_online_devices(self) -> List[KVMDevice]:
        """Get online devices"""
        return [d for d in self.devices.values() if d.status == DeviceStatus.ONLINE]

    def get_offline_devices(self) -> List[KVMDevice]:
        """Get offline devices"""
        return [d for d in self.devices.values() if d.status == DeviceStatus.OFFLINE]

    # ==================== Batch Operations ====================

    def connect_all(self, parallel: bool = True) -> Dict[str, bool]:
        """Connect to all devices"""
        results = {}

        if parallel:
            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                futures = {executor.submit(d.connect): d.name for d in self.devices.values()}
                for future in as_completed(futures):
                    name = futures[future]
                    try:
                        results[name] = future.result()
                    except Exception as e:
                        results[name] = False
                        print(f"[{name}] Connection error: {e}")
        else:
            for device in self.devices.values():
                results[device.name] = device.connect()

        return results

    def disconnect_all(self):
        """Disconnect all devices"""
        for device in self.devices.values():
            device.disconnect()

    def refresh_status_all(self) -> Dict[str, dict]:
        """Refresh status of all devices"""
        results = {}

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {executor.submit(self._get_device_status, d): d.name for d in self.devices.values()}
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

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {executor.submit(func, d, *args, **kwargs): d.name for d in self.devices.values()}
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
        for device in self.devices.values():
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

        if device_name in self.devices:
            self.devices[device_name].info.group = group_name

    # ==================== Statistics ====================

    def get_statistics(self) -> dict:
        """Get overall statistics"""
        total = len(self.devices)
        online = len([d for d in self.devices.values() if d.status == DeviceStatus.ONLINE])
        offline = total - online

        groups = {}
        for device in self.devices.values():
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
