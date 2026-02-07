"""
WellcomLAND API 클라이언트
서버와 통신하여 인증 및 기기 목록을 관리
"""
import requests
from typing import Optional
from config import settings


class APIClient:
    """서버 API 클라이언트"""

    def __init__(self):
        self._base_url = settings.get('server.api_url', 'http://log.wellcomll.org:8000')
        self._token: str = settings.get('server.token', '')
        self._user: Optional[dict] = None

    @property
    def is_logged_in(self) -> bool:
        return bool(self._token and self._user)

    @property
    def user(self) -> Optional[dict]:
        return self._user

    @property
    def is_admin(self) -> bool:
        return self._user.get('role') == 'admin' if self._user else False

    def _headers(self) -> dict:
        h = {'Content-Type': 'application/json'}
        if self._token:
            h['Authorization'] = f'Bearer {self._token}'
        return h

    def _get(self, path: str) -> dict:
        r = requests.get(f'{self._base_url}{path}', headers=self._headers(), timeout=10)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, data: dict) -> dict:
        r = requests.post(f'{self._base_url}{path}', json=data, headers=self._headers(), timeout=10)
        r.raise_for_status()
        return r.json()

    def _put(self, path: str, data: dict) -> dict:
        r = requests.put(f'{self._base_url}{path}', json=data, headers=self._headers(), timeout=10)
        r.raise_for_status()
        return r.json()

    def _delete(self, path: str) -> dict:
        r = requests.delete(f'{self._base_url}{path}', headers=self._headers(), timeout=10)
        r.raise_for_status()
        return r.json()

    # === Auth ===
    def login(self, username: str, password: str) -> dict:
        """로그인 → JWT 토큰 + 사용자 정보 반환"""
        data = self._post('/api/auth/login', {'username': username, 'password': password})
        self._token = data['token']
        self._user = data['user']
        # 토큰 저장
        settings.set('server.token', self._token)
        settings.set('server.username', username)
        return data

    def verify_token(self) -> bool:
        """저장된 토큰 유효성 확인"""
        if not self._token:
            return False
        try:
            user = self._get('/api/auth/me')
            self._user = user
            return True
        except Exception:
            self._token = ''
            settings.set('server.token', '')
            return False

    def logout(self):
        """로그아웃"""
        self._token = ''
        self._user = None
        settings.set('server.token', '')

    # === Devices (일반 사용자) ===
    def get_my_devices(self) -> list:
        """내게 할당된 기기 목록"""
        return self._get('/api/devices')

    # === Device Sync (로컬 → 서버) ===
    def sync_device_to_server(self, device_data: dict) -> Optional[dict]:
        """로컬 기기를 서버에 동기화 (admin 전용)
        이미 존재하면 스킵, 없으면 추가
        """
        if not self.is_admin:
            return None
        try:
            return self._post('/api/admin/devices', device_data)
        except requests.exceptions.HTTPError as e:
            # 409 Conflict = 이미 존재 → 무시
            if e.response is not None and e.response.status_code == 409:
                return None
            raise

    def sync_devices_to_server(self, devices: list) -> dict:
        """여러 기기를 서버에 일괄 동기화
        Returns: {'synced': int, 'skipped': int, 'failed': int}
        """
        result = {'synced': 0, 'skipped': 0, 'failed': 0}
        if not self.is_admin:
            return result

        # 서버에 이미 있는 기기 IP 목록
        try:
            existing = self.admin_get_all_devices()
            existing_ips = {d['ip'] for d in existing}
            existing_names = {d['name'] for d in existing}
        except Exception:
            existing_ips = set()
            existing_names = set()

        for dev in devices:
            ip = dev.get('ip', '')
            name = dev.get('name', '')
            if ip in existing_ips or name in existing_names:
                result['skipped'] += 1
                continue
            try:
                self.sync_device_to_server(dev)
                result['synced'] += 1
                existing_ips.add(ip)
                existing_names.add(name)
            except Exception as e:
                print(f"[Sync] 기기 동기화 실패 ({name}/{ip}): {e}")
                result['failed'] += 1

        return result

    # === Admin: Devices ===
    def admin_get_all_devices(self) -> list:
        return self._get('/api/admin/devices')

    def admin_create_device(self, data: dict) -> dict:
        return self._post('/api/admin/devices', data)

    def admin_update_device(self, device_id: int, data: dict) -> dict:
        return self._put(f'/api/admin/devices/{device_id}', data)

    def admin_delete_device(self, device_id: int) -> dict:
        return self._delete(f'/api/admin/devices/{device_id}')

    # === Admin: Users ===
    def admin_get_users(self) -> list:
        return self._get('/api/admin/users')

    def admin_create_user(self, data: dict) -> dict:
        return self._post('/api/admin/users', data)

    def admin_update_user(self, user_id: int, data: dict) -> dict:
        return self._put(f'/api/admin/users/{user_id}', data)

    def admin_delete_user(self, user_id: int) -> dict:
        return self._delete(f'/api/admin/users/{user_id}')

    # === Admin: Device Assignment ===
    def admin_get_user_devices(self, user_id: int) -> list:
        return self._get(f'/api/admin/users/{user_id}/devices')

    def admin_assign_devices(self, user_id: int, device_ids: list, permission: str = 'control') -> dict:
        return self._post(f'/api/admin/users/{user_id}/devices', {
            'device_ids': device_ids,
            'permission': permission,
        })

    # === Admin: Groups ===
    def admin_get_groups(self) -> list:
        return self._get('/api/admin/groups')

    def admin_create_group(self, data: dict) -> dict:
        return self._post('/api/admin/groups', data)

    # === Files (Cloud Drive) ===
    def upload_file(self, filepath: str, progress_callback=None) -> dict:
        """파일 업로드 (multipart) — 쿼타 사전 체크 포함"""
        import os
        h = {}
        if self._token:
            h['Authorization'] = f'Bearer {self._token}'

        filename = os.path.basename(filepath)
        file_size = os.path.getsize(filepath)

        # 쿼타 사전 체크
        try:
            quota_info = self.get_quota()
            q = quota_info.get('quota')
            if q == 0:
                raise Exception("클라우드 저장소 접근 권한이 없습니다")
            if q is not None:
                remaining = quota_info.get('remaining', 0)
                if file_size > remaining:
                    raise Exception(
                        f"클라우드 저장 용량 초과\n"
                        f"파일 크기: {file_size // (1024*1024)}MB\n"
                        f"남은 용량: {remaining // (1024*1024)}MB"
                    )
        except requests.exceptions.HTTPError:
            pass  # 쿼타 API 실패 시 서버에서 최종 체크

        with open(filepath, 'rb') as f:
            files = {'file': (filename, f, 'application/octet-stream')}
            r = requests.post(
                f'{self._base_url}/api/files/upload',
                headers=h,
                files=files,
                timeout=300,
            )
        r.raise_for_status()
        return r.json()

    def get_files(self) -> list:
        """내 파일 목록"""
        return self._get('/api/files')

    def get_file_download_url(self, file_id: int) -> str:
        """파일 다운로드 URL (토큰 포함)"""
        return f'{self._base_url}/api/files/{file_id}/download'

    def delete_file(self, file_id: int) -> dict:
        """파일 삭제"""
        return self._delete(f'/api/files/{file_id}')

    def get_quota(self) -> dict:
        """내 클라우드 쿼타 조회"""
        return self._get('/api/files/quota')

    # === Version ===
    def get_server_version(self) -> dict:
        return self._get('/api/version')

    def health_check(self) -> bool:
        try:
            r = self._get('/api/health')
            return r.get('status') == 'ok'
        except Exception:
            return False


# 싱글톤
api_client = APIClient()
