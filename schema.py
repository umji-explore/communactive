"""DB 구조 자동 보강

db.create_all() 은 새 테이블만 만들고, 이미 있는 테이블에 새 컬럼은 추가하지 않는다.
그래서 기존 데이터를 지우지 않고 빠진 컬럼만 ALTER TABLE 로 더한다. (여러 번 실행해도 안전)
"""
from sqlalchemy import inspect, text

# (테이블, 컬럼, Postgres 타입, SQLite 타입)
NEW_COLUMNS = [
    ("user", "avatar", "VARCHAR(500)", "VARCHAR(500)"),
    ("community", "icon", "VARCHAR(500)", "VARCHAR(500)"),
    ("community", "banner", "VARCHAR(500)", "VARCHAR(500)"),
    ("community", "categories", "VARCHAR(300) DEFAULT ''", "VARCHAR(300) DEFAULT ''"),
    ("post", "category", "VARCHAR(20)", "VARCHAR(20)"),
    ("post", "is_notice", "BOOLEAN NOT NULL DEFAULT FALSE", "BOOLEAN NOT NULL DEFAULT 0"),
    ("post", "has_image", "BOOLEAN NOT NULL DEFAULT FALSE", "BOOLEAN NOT NULL DEFAULT 0"),
    ("post", "has_poll", "BOOLEAN NOT NULL DEFAULT FALSE", "BOOLEAN NOT NULL DEFAULT 0"),
]


def upgrade(db):
    db.create_all()
    engine = db.engine
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    pg = engine.dialect.name == "postgresql"
    with engine.begin() as conn:
        for table, col, pg_type, lite_type in NEW_COLUMNS:
            if table not in tables:
                continue
            existing = {c["name"] for c in insp.get_columns(table)}
            if col in existing:
                continue
            if pg:
                conn.execute(text(f'ALTER TABLE "{table}" ADD COLUMN IF NOT EXISTS {col} {pg_type}'))
            else:
                conn.execute(text(f'ALTER TABLE "{table}" ADD COLUMN {col} {lite_type}'))
