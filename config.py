import os

from dotenv import load_dotenv

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))


def _int(name, default):
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-me")
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL",
        "sqlite:///" + os.path.join(BASE_DIR, "instance", "communactive.db"),
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # 업로드 (커뮤콘 이미지)
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
    DEV_LOGIN = os.environ.get("DEV_LOGIN", "0") == "1"

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
    POPULAR_MIN_UPVOTES = _int("POPULAR_MIN_UPVOTES", 3)  # 커뮤니 인기글 기준 추천 수
    REALTIME_HOURS = _int("REALTIME_HOURS", 3)            # 실시간 베스트 집계 시간
    POSTS_PER_PAGE = 20
