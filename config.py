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

    # 검색엔진 (구글/네이버)
    SITE_URL = os.environ.get("SITE_URL", "https://communactive.vercel.app").rstrip("/")
    GOOGLE_SITE_VERIFICATION = os.environ.get("GOOGLE_SITE_VERIFICATION", "")  # 구글 서치 콘솔 소유 확인 코드
    NAVER_SITE_VERIFICATION = os.environ.get("NAVER_SITE_VERIFICATION", "")    # 네이버 서치어드바이저 소유 확인 코드
    # 검색엔진 소유 확인용 HTML 파일 이름 (확장자 .html 제외)
    SITE_VERIFICATION_FILES = {"googlee3d321a70028486d"}

    # 업로드 (이미지): Vercel Blob 이 연결돼 있으면 Blob, 아니면 로컬 static/uploads
    # 사진은 한 장씩 따로 올린다 (Vercel 은 요청 하나당 4.5MB 제한)
    USE_BLOB = bool(os.environ.get("BLOB_READ_WRITE_TOKEN") or os.environ.get("BLOB_STORE_ID"))
    UPLOAD_DIR = os.path.join(BASE_DIR, "static", "uploads")
    MAX_CONTENT_LENGTH = int(4.5 * 1024 * 1024)
    CON_MAX_FILES = 30                      # 콘 하나당 최대 이미지 수
    CON_MAX_PRICE = 100000
    POST_MAX_IMAGES = 30                    # 글 하나당 최대 사진 수
    VIDEO_MAX_BYTES = _int("VIDEO_MAX_MB", 100) * 1024 * 1024  # 동영상 최대 크기 (브라우저 → Blob 직접 업로드)

    # 계급
    ELDER_POSTS = _int("ELDER_POSTS", 500)  # 이 개수 이상 글을 쓰면 장로 회원 (보라색)

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
