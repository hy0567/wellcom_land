#!/bin/bash
# =============================================================
# WellcomLAND 서버 배포 스크립트
# 사용법: ssh -p 2222 root@log.wellcomll.org 'bash -s' < deploy.sh
# 또는 서버에서 직접: bash deploy.sh
# =============================================================
set -e

echo "=========================================="
echo " WellcomLAND Server Deploy v1.9.0"
echo "=========================================="

# ---- 서버 코드 경로 자동 탐색 ----
SERVER_DIR=""
CANDIDATES=(
    "/opt/wellcomland/server"
    "/root/wellcomland/server"
    "/home/wellcom/server"
    "/srv/wellcomland/server"
)

# 실행 중인 프로세스에서 경로 추출 시도
RUNNING_PATH=$(ps aux | grep 'uvicorn\|main:app\|python.*main.py' | grep -v grep | head -1 | sed 's/.*--chdir \([^ ]*\).*/\1/' 2>/dev/null || true)
if [ -n "$RUNNING_PATH" ] && [ -d "$RUNNING_PATH" ]; then
    CANDIDATES=("$RUNNING_PATH" "${CANDIDATES[@]}")
fi

# lsof로 main.py 위치 찾기
LSOF_PATH=$(lsof -p $(pgrep -f 'uvicorn\|main:app' | head -1) 2>/dev/null | grep 'main.py' | awk '{print $NF}' | head -1 | xargs dirname 2>/dev/null || true)
if [ -n "$LSOF_PATH" ] && [ -d "$LSOF_PATH" ]; then
    CANDIDATES=("$LSOF_PATH" "${CANDIDATES[@]}")
fi

# find로 찾기
FIND_PATH=$(find / -maxdepth 5 -name "main.py" -path "*/server/*" -not -path "*/site-packages/*" -not -path "*/.local/*" 2>/dev/null | head -1 | xargs dirname 2>/dev/null || true)
if [ -n "$FIND_PATH" ] && [ -d "$FIND_PATH" ]; then
    CANDIDATES=("$FIND_PATH" "${CANDIDATES[@]}")
fi

for dir in "${CANDIDATES[@]}"; do
    if [ -f "$dir/main.py" ] && [ -f "$dir/config.py" ]; then
        SERVER_DIR="$dir"
        break
    fi
done

if [ -z "$SERVER_DIR" ]; then
    echo "[ERROR] 서버 코드 디렉토리를 찾을 수 없습니다."
    echo "직접 지정: SERVER_DIR=/path/to/server bash deploy.sh"
    exit 1
fi

echo "[INFO] 서버 디렉토리: $SERVER_DIR"

# ---- Git pull (git repo인 경우) ----
REPO_DIR=$(dirname "$SERVER_DIR")
if [ -d "$REPO_DIR/.git" ]; then
    echo "[STEP 1] Git pull..."
    cd "$REPO_DIR"
    git pull origin main 2>&1 || echo "[WARN] git pull 실패 — 수동 파일 복사로 진행"
else
    echo "[STEP 1] Git 아님 — 파일 직접 복사 필요"
fi

# ---- .env 파일 생성 ----
echo "[STEP 2] .env 파일 생성..."
cat > "$SERVER_DIR/.env" << 'ENVEOF'
# WellcomLAND Server 환경변수
# =========================================================

# MySQL
DB_HOST=127.0.0.1
DB_PORT=3306
DB_USER=wellcom_api
DB_PASS=Wellcom@API2026!
DB_NAME=wellcomland

# JWT
JWT_SECRET=wellcomland-jwt-secret-key-2026-change-in-production

# Server
API_HOST=0.0.0.0
API_PORT=8000

# File Storage
UPLOAD_DIR=/opt/wellcomland/uploads

# Tailscale (회사 공용 계정 - ghdyd0567@gmail.com)
TAILSCALE_AUTHKEY=tskey-auth-kyaZwNxLUa11CNTRL-pFBEvigZ5m2REQRrSiE4m211EnJ4JxbJ
TAILSCALE_API_TOKEN=tskey-api-kTCQDpaFUc11CNTRL-6wCCgT14oTWuBsEi4eeoTWCwZ4nztF8L
TAILSCALE_TAILNET=-
ENVEOF

chmod 600 "$SERVER_DIR/.env"
echo "[INFO] .env 파일 생성 완료 (권한: 600)"

# ---- python-dotenv 설치 확인 ----
echo "[STEP 3] python-dotenv 확인..."
python3 -c "import dotenv; print('[INFO] python-dotenv 설치됨')" 2>/dev/null || {
    echo "[INFO] python-dotenv 설치 중..."
    pip3 install python-dotenv 2>&1
}

# ---- 서버 재시작 ----
echo "[STEP 4] 서버 재시작..."

# systemd 서비스인지 확인
if systemctl is-active --quiet wellcomland 2>/dev/null; then
    systemctl restart wellcomland
    echo "[INFO] systemd 서비스 재시작 완료"
elif systemctl is-active --quiet wellcom-api 2>/dev/null; then
    systemctl restart wellcom-api
    echo "[INFO] systemd 서비스 재시작 완료"
else
    # 직접 프로세스 재시작
    echo "[INFO] uvicorn 프로세스 재시작..."
    pkill -f "uvicorn.*main:app" 2>/dev/null || pkill -f "python.*main.py" 2>/dev/null || true
    sleep 2

    cd "$SERVER_DIR"
    nohup python3 main.py > /var/log/wellcomland.log 2>&1 &
    echo "[INFO] 서버 시작됨 (PID: $!)"
fi

# ---- 확인 ----
sleep 3
echo "[STEP 5] 서버 상태 확인..."
RESPONSE=$(curl -s --connect-timeout 5 http://localhost:8000/api/version 2>/dev/null || echo "FAIL")
echo "[INFO] 서버 응답: $RESPONSE"

echo ""
echo "=========================================="
echo " 배포 완료!"
echo "=========================================="
