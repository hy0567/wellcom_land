"""
WellcomLAND API 클라이언트
서버와 통신하여 인증 및 기기 목록을 관리
"""
import os
import requests
from typing import Optional
from config import settings


def _tailscale_exe() -> str:
    """Tailscale CLI 경로 반환 (PATH에 없어도 동작)"""
    for path in [
        os.path.join(os.environ.get('ProgramFiles', r'C:\Program Files'), 'Tailscale', 'tailscale.exe'),
        os.path.join(os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)'), 'Tailscale', 'tailscale.exe'),
        r'C:\Program Files\Tailscale\tailscale.exe',
    ]:
        if os.path.isfile(path):
            return path
    return 'tailscale'  # PATH에서 찾기 폴백


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

        # Tailscale 상태 확인 (백그라운드)
        import threading
        threading.Thread(target=self._check_tailscale, daemon=True).start()

        return data

    def _check_tailscale(self):
        """Tailscale 연결 상태 확인 + authkey로 자동 참여 + subnet route 광고"""
        try:
            import subprocess
            import json as _json
            import socket

            # 1. Tailscale 설치 확인
            r = subprocess.run(
                [_tailscale_exe(), 'version'],
                capture_output=True, text=True, encoding='utf-8', errors='replace',
                timeout=5, creationflags=0x08000000
            )
            if r.returncode != 0:
                print("[Tailscale] 미설치 — 건너뜀")
                return

            # 2. Tailscale 상태 확인
            r = subprocess.run(
                [_tailscale_exe(), 'status', '--json'],
                capture_output=True, text=True, encoding='utf-8', errors='replace',
                timeout=10, creationflags=0x08000000
            )

            backend_state = ''
            self_ip = ''
            # stdout 또는 stderr에서 JSON 파싱 (Tailscale 버전에 따라 다름)
            json_text = r.stdout.strip() or r.stderr.strip()
            if json_text:
                try:
                    status = _json.loads(json_text)
                    self_ip = status.get('Self', {}).get('TailscaleIPs', [''])[0]
                    backend_state = status.get('BackendState', '')
                except Exception:
                    pass

            print(f"[Tailscale] 상태: {backend_state}, IP: {self_ip}")

            # 3. tailnet 미참여 시 authkey로 자동 참여
            needs_join = backend_state in ('NeedsLogin', 'NoState', 'Stopped', '')

            if needs_join:
                print("[Tailscale] tailnet 미참여 — authkey로 자동 참여 시도")
                authkey = self._get_tailscale_authkey()

                if authkey:
                    # LAN 서브넷 감지 → advertise-routes 포함
                    lan_subnet = self._detect_lan_subnet()
                    cmd = [_tailscale_exe(), 'up', f'--authkey={authkey}',
                           '--accept-routes', '--reset']
                    if lan_subnet:
                        cmd.append(f'--advertise-routes={lan_subnet}')
                        print(f"[Tailscale] 서브넷 광고 예정: {lan_subnet}")

                    r = subprocess.run(
                        cmd, capture_output=True, text=True, encoding='utf-8', errors='replace',
                        timeout=30, creationflags=0x08000000
                    )
                    if r.returncode == 0:
                        print("[Tailscale] authkey로 tailnet 참여 완료!")
                        settings.set('tailscale.joined', True)
                        # 서브넷 라우트 자동 승인 (API)
                        if lan_subnet:
                            self._auto_approve_subnet_routes()
                    else:
                        print(f"[Tailscale] authkey 참여 실패: {r.stderr.strip()}")
                else:
                    print("[Tailscale] 서버에 authkey 미설정 — 수동 로그인 필요")
            else:
                # 4. 이미 참여 중이면 accept-routes + advertise-routes
                lan_subnet = self._detect_lan_subnet()
                cmd = [_tailscale_exe(), 'up', '--accept-routes']
                if lan_subnet:
                    cmd.append(f'--advertise-routes={lan_subnet}')

                subprocess.run(
                    cmd, capture_output=True, text=True, encoding='utf-8', errors='replace',
                    timeout=15, creationflags=0x08000000
                )
                print(f"[Tailscale] accept-routes 활성화 완료" +
                      (f", advertise-routes={lan_subnet}" if lan_subnet else ""))

                # 서브넷 라우트 자동 승인
                if lan_subnet:
                    self._auto_approve_subnet_routes()

        except Exception as e:
            print(f"[Tailscale] 상태 확인 오류 (무시): {e}")

    def _detect_lan_subnet(self) -> str:
        """현재 PC의 LAN 서브넷 감지 (Tailscale/APIPA 제외)

        Returns:
            "192.168.68.0/24" 형태의 서브넷, 없으면 빈 문자열
        """
        try:
            import socket
            hostname = socket.gethostname()
            ips = socket.getaddrinfo(hostname, None, socket.AF_INET)
            subnets = []
            for info in ips:
                ip = info[4][0]
                # Tailscale(100.x), APIPA(169.254), loopback(127) 제외
                if ip.startswith('100.') or ip.startswith('169.254.') or ip.startswith('127.'):
                    continue
                parts = ip.split('.')
                subnet = f"{parts[0]}.{parts[1]}.{parts[2]}.0/24"
                if subnet not in subnets:
                    subnets.append(subnet)
                    print(f"[Tailscale] LAN 감지: {ip} → {subnet}")
            return ','.join(subnets) if subnets else ''
        except Exception as e:
            print(f"[Tailscale] LAN 서브넷 감지 실패: {e}")
            return ''

    def _auto_approve_subnet_routes(self):
        """Tailscale API로 이 디바이스의 서브넷 라우트 자동 승인

        ★ tailscale up --advertise-routes 후 API에 전파되기까지 지연이 있으므로
           API의 advertisedRoutes 대신 로컬에서 감지한 서브넷을 직접 승인.
        """
        try:
            import subprocess
            import json as _json
            import requests as _req

            # 현재 디바이스의 Tailscale 호스트명
            r = subprocess.run(
                [_tailscale_exe(), 'status', '--json'],
                capture_output=True, text=True, encoding='utf-8', errors='replace',
                timeout=10, creationflags=0x08000000
            )
            # tailscale status --json은 stdout 또는 stderr로 출력될 수 있음
            json_text = r.stdout.strip() or r.stderr.strip()
            if not json_text:
                print("[Tailscale] status --json 출력 없음")
                return

            status = _json.loads(json_text)
            self_hostname = status.get('Self', {}).get('HostName', '')

            if not self_hostname:
                return

            # 로컬에서 감지한 서브넷 (API 전파 지연 문제 우회)
            local_subnets = self._detect_lan_subnet()
            if not local_subnets:
                print("[Tailscale] 서브넷 없음 — 라우트 승인 건너뜀")
                return

            routes_to_approve = [s.strip() for s in local_subnets.split(',') if s.strip()]

            # Tailscale API 토큰
            api_token = settings.get('tailscale.api_token', '')
            if not api_token:
                api_token = 'tskey-api-kTCQDpaFUc11CNTRL-6wCCgT14oTWuBsEi4eeoTWCwZ4nztF8L'

            headers = {'Authorization': f'Bearer {api_token}'}

            # 디바이스 목록에서 내 device ID 찾기
            resp = _req.get(
                'https://api.tailscale.com/api/v2/tailnet/-/devices',
                headers=headers, timeout=10
            )
            if resp.status_code != 200:
                print(f"[Tailscale] 디바이스 목록 조회 실패: {resp.status_code}")
                return

            devices = resp.json().get('devices', [])
            for dev in devices:
                if dev.get('hostname', '').upper() == self_hostname.upper():
                    device_id = dev.get('id', '')
                    if not device_id:
                        break

                    # API의 advertisedRoutes와 로컬 서브넷 합치기
                    adv_routes = dev.get('advertisedRoutes', [])
                    all_routes = list(set(adv_routes + routes_to_approve))

                    # 라우트 승인 요청 (advertise + enable 동시)
                    approve_resp = _req.post(
                        f'https://api.tailscale.com/api/v2/device/{device_id}/routes',
                        headers=headers,
                        json={'routes': all_routes},
                        timeout=10
                    )
                    if approve_resp.status_code == 200:
                        result = approve_resp.json()
                        print(f"[Tailscale] 서브넷 라우트 승인 완료: "
                              f"advertised={result.get('advertisedRoutes')}, "
                              f"enabled={result.get('enabledRoutes')}")
                    else:
                        print(f"[Tailscale] 라우트 승인 실패: {approve_resp.status_code} "
                              f"{approve_resp.text[:200]}")
                    break

        except Exception as e:
            print(f"[Tailscale] 서브넷 라우트 자동 승인 오류 (무시): {e}")

    def _get_tailscale_authkey(self) -> str:
        """서버에서 Tailscale authkey 조회"""
        try:
            data = self._get('/api/tailscale/authkey')
            authkey = data.get('authkey', '')
            if authkey:
                # 로컬에도 캐시
                settings.set('tailscale.authkey', authkey)
            return authkey
        except Exception as e:
            print(f"[Tailscale] authkey 조회 실패: {e}")
            # 로컬 캐시에서 폴백
            return settings.get('tailscale.authkey', '')

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

    # === KVM Registry (원격 장치 공유) ===
    def register_kvm_devices(self, devices: list, relay_ip: str, location: str = "") -> dict:
        """관제 PC가 발견한 KVM을 서버에 등록"""
        return self._post('/api/kvm/register', {
            'devices': devices,
            'relay_ip': relay_ip,
            'location': location,
        })

    def get_remote_kvm_list(self) -> list:
        """서버에서 원격 KVM 목록 조회 (Tailscale 경유 접근 정보 포함)"""
        try:
            data = self._get('/api/kvm/list')
            return data.get('devices', [])
        except Exception:
            return []

    def send_kvm_heartbeat(self, relay_ip: str) -> dict:
        """관제 PC heartbeat 전송"""
        return self._post('/api/kvm/heartbeat', {'relay_ip': relay_ip})

    # === Tailscale (admin) ===
    def admin_set_tailscale_authkey(self, authkey: str) -> dict:
        """Tailscale authkey 설정 (admin 전용)"""
        return self._put('/api/admin/tailscale/authkey', {'authkey': authkey})

    def get_tailscale_authkey(self) -> str:
        """서버에서 Tailscale authkey 조회"""
        try:
            data = self._get('/api/tailscale/authkey')
            return data.get('authkey', '')
        except Exception:
            return ''

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
