import os

from dotenv import load_dotenv

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))


def _int(name, default):
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


ON_VERCEL = bool(os.environ.get("VERCEL"))


def _database_url():
    url = os.environ.get("DATABASE_URL")
    if not url:
        if ON_VERCEL:
            raise RuntimeError("Vercel 에서는 DATABASE_URL (Neon Postgres) 이 필요합니다.")
        return "sqlite:///" + os.path.join(BASE_DIR, "instance", "communactive.db")
    # Neon 등은 postgres:// 로 주기 때문에 SQLAlchemy 용 드라이버 이름으로 바꿔준다
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def _secret_key():
    key = os.environ.get("SECRET_KEY")
    if not key and ON_VERCEL:
        raise RuntimeError("Vercel 환경변수에 SECRET_KEY 를 설정하세요.")
    return key or "dev-secret-change-me"


class Config:
    SECRET_KEY = _secret_key()
    SQLALCHEMY_DATABASE_URI = _database_url()
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SESSION_COOKIE_SECURE = ON_VERCEL
    PREFERRED_URL_SCHEME = "https" if ON_VERCEL else "http"

    # 업로드 (커뮤콘 이미지): Vercel Blob 이 연결돼 있으면 Blob, 아니면 로컬 static/uploads
    USE_BLOB = bool(os.environ.get("BLOB_READ_WRITE_TOKEN") or os.environ.get("BLOB_STORE_ID"))
    UPLOAD_DIR = os.path.join(BASE_DIR, "static", "uploads")
    MAX_CONTENT_LENGTH = 40 * 1024 * 1024  # 요청 전체 최대 40MB
    CON_MAX_FILES = 30                      # 콘 하나당 최대 이미지 수
    CON_MAX_FILE_SIZE = 1 * 1024 * 1024     # 이미지 1장당 최대 1MB
    CON_MAX_PRICE = 100000

    # 소셜 로그인 (.env 에 키를 넣으면 해당 버튼이 활성화됨)
    GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
    GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
    NAVER_CLIENT_ID = os.environ.get("NAVER_CLIENT_ID", "")
    NAVER_CLIENT_SECRET = os.environ.get("NAVER_CLIENT_SECRET", "")
    FACEBOOK_CLIENT_ID = os.environ.get("FACEBOOK_CLIENT_ID", "")
    FACEBOOK_CLIENT_SECRET = os.environ.get("FACEBOOK_CLIENT_SECRET", "")

    # 개발용 로그인 (소셜 키 없이 테스트할 때만 켜기. 운영에서는 반드시 0)
    DEV_LOGIN = os.environ.get("DEV_LOGIN", "0") == "1" and not ON_VERCEL  # 배포 환경에선 절대 켜지지 않음

    # 포인트
    POINTS_POST = _int("POINTS_POST", 200)
    POINTS_COMMENT = _int("POINTS_COMMENT", 50)
    # 하루에 포인트를 받을 수 있는 최대 횟수 (0 = 무제한)
    DAILY_POST_POINT_LIMIT = _int("DAILY_POST_POINT_LIMIT", 0)
    DAILY_COMMENT_POINT_LIMIT = _int("DAILY_COMMENT_POINT_LIMIT", 0)

    # 도배 방지 (초)
    POST_COOLDOWN = _int("POST_COOLDOWN", 30)
    COMMENT_COOLDOWN = _int("COMMENT_COOLDOWN", 10)

    # 인기글 / 베스트
    POPULAR_MIN_UPVOTES = _int("POPULAR_MIN_UPVOTES", 3)  # 커뮤니티 인기글 기준 추천 수
    REALTIME_HOURS = _int("REALTIME_HOURS", 3)            # 실시간 베스트 집계 시간
    POSTS_PER_PAGE = 20
