"""
SQLite Database for KVM Device Management
"""

import sqlite3
import os
from typing import List, Optional
from contextlib import contextmanager

from config import DB_PATH


class Database:
    """SQLite Database Manager"""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._ensure_directory()
        self._init_db()

    def _ensure_directory(self):
        """Ensure database directory exists"""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

    @contextmanager
    def _get_connection(self):
        """Get database connection context manager"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self):
        """Initialize database tables"""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # KVM Devices table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS devices (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    ip TEXT NOT NULL,
                    port INTEGER DEFAULT 22,
                    web_port INTEGER DEFAULT 80,
                    username TEXT DEFAULT 'root',
                    password TEXT DEFAULT 'luckfox',
                    group_name TEXT DEFAULT 'default',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # Groups table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS groups (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    description TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # Insert default group
            cursor.execute('''
                INSERT OR IGNORE INTO groups (name, description)
                VALUES ('default', 'Default group')
            ''')

            conn.commit()

    # ==================== Device CRUD ====================

    def add_device(self, name: str, ip: str, port: int = 22, web_port: int = 80,
                   username: str = "root", password: str = "luckfox",
                   group_name: str = "default") -> int:
        """Add new KVM device"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO devices (name, ip, port, web_port, username, password, group_name)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (name, ip, port, web_port, username, password, group_name))
            conn.commit()
            return cursor.lastrowid

    def update_device(self, device_id: int, **kwargs):
        """Update device information"""
        allowed_fields = ['name', 'ip', 'port', 'web_port', 'username', 'password', 'group_name']
        updates = {k: v for k, v in kwargs.items() if k in allowed_fields}

        if not updates:
            return

        set_clause = ', '.join(f"{k} = ?" for k in updates.keys())
        values = list(updates.values()) + [device_id]

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f'''
                UPDATE devices SET {set_clause}, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', values)
            conn.commit()

    def delete_device(self, device_id: int):
        """Delete device by ID"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM devices WHERE id = ?', (device_id,))
            conn.commit()

    def get_device(self, device_id: int) -> Optional[dict]:
        """Get device by ID"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM devices WHERE id = ?', (device_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_device_by_name(self, name: str) -> Optional[dict]:
        """Get device by name"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM devices WHERE name = ?', (name,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_device_by_ip(self, ip: str) -> Optional[dict]:
        """Get device by IP"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM devices WHERE ip = ?', (ip,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_all_devices(self) -> List[dict]:
        """Get all devices"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM devices ORDER BY group_name, name')
            return [dict(row) for row in cursor.fetchall()]

    def get_devices_by_group(self, group_name: str) -> List[dict]:
        """Get devices by group"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM devices WHERE group_name = ? ORDER BY name', (group_name,))
            return [dict(row) for row in cursor.fetchall()]

    # ==================== Group CRUD ====================

    def add_group(self, name: str, description: str = "") -> int:
        """Add new group"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO groups (name, description) VALUES (?, ?)
            ''', (name, description))
            conn.commit()
            return cursor.lastrowid

    def delete_group(self, group_name: str):
        """Delete group (moves devices to default)"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            # Move devices to default group
            cursor.execute('''
                UPDATE devices SET group_name = 'default' WHERE group_name = ?
            ''', (group_name,))
            # Delete group
            cursor.execute('DELETE FROM groups WHERE name = ? AND name != "default"', (group_name,))
            conn.commit()

    def get_all_groups(self) -> List[dict]:
        """Get all groups"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM groups ORDER BY name')
            return [dict(row) for row in cursor.fetchall()]

    def get_device_count(self) -> int:
        """Get total device count"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT COUNT(*) FROM devices')
            return cursor.fetchone()[0]
