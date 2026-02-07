"""
WellcomLAND API 서버 설정
"""
import os

# MySQL
DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_USER = os.getenv("DB_USER", "wellcom_api")
DB_PASS = os.getenv("DB_PASS", "Wellcom@API2026!")
DB_NAME = os.getenv("DB_NAME", "wellcomland")

# JWT
JWT_SECRET = os.getenv("JWT_SECRET", "wellcomland-jwt-secret-key-2026-change-in-production")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = 24

# Server
API_HOST = os.getenv("API_HOST", "0.0.0.0")
API_PORT = int(os.getenv("API_PORT", "8000"))

# File Storage
UPLOAD_DIR = os.getenv("UPLOAD_DIR", "/opt/wellcomland/uploads")
MAX_FILE_SIZE = 500 * 1024 * 1024  # 500MB
