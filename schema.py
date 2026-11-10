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
    ("post", "has_video", "BOOLEAN NOT NULL DEFAULT FALSE", "BOOLEAN NOT NULL DEFAULT 0"),
    ("post", "board_id", "INTEGER", "INTEGER"),
    ("post", "series_id", "INTEGER", "INTEGER"),
    ("post", "fmt", "VARCHAR(10) NOT NULL DEFAULT 'text'", "VARCHAR(10) NOT NULL DEFAULT 'text'"),
    ("post", "text", "TEXT", "TEXT"),
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

        # 예전 글: 검색용 글자 채우기 (예전 글은 본문이 곧 글자)
        conn.execute(text("UPDATE post SET text = content WHERE text IS NULL AND fmt = 'text'"))

        # 예전 '말머리' → 게시판으로 옮기기 (그 커뮤니티에 게시판이 하나도 없을 때만, 한 번)
        rows = conn.execute(text("SELECT id, categories FROM community WHERE categories IS NOT NULL AND categories <> ''")).fetchall()
        for cid, cats in rows:
            if conn.execute(text("SELECT COUNT(*) FROM board WHERE community_id = :c"), {"c": cid}).scalar():
                continue
            for i, name in enumerate([c for c in cats.split("\n") if c][:20]):
                conn.execute(text("INSERT INTO board (community_id, name, sort_order, manager_only, created_at) "
                                  "VALUES (:c, :n, :o, :f, CURRENT_TIMESTAMP)"),
                             {"c": cid, "n": name[:20], "o": i, "f": False})
                bid = conn.execute(text("SELECT id FROM board WHERE community_id = :c AND name = :n ORDER BY id DESC"),
                                   {"c": cid, "n": name[:20]}).scalar()
                conn.execute(text("UPDATE post SET board_id = :b WHERE community_id = :c AND category = :n AND board_id IS NULL"),
                             {"b": bid, "c": cid, "n": name})
            conn.execute(text("UPDATE community SET categories = '' WHERE id = :c"), {"c": cid})
