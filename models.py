from datetime import datetime, timezone

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


def utcnow():
    """DB에는 timezone 없는 UTC 시각으로 저장한다."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(20), unique=True)  # 식별 코드 (로그인 아이디, 소문자). 탈퇴하면 NULL
    nickname = db.Column(db.String(20), unique=True, nullable=False)
    password_hash = db.Column(db.String(255))
    recovery_hash = db.Column(db.String(255))  # 비밀번호 찾기용 복구 코드 (해시만 저장)
    avatar = db.Column(db.String(500))  # 프로필 사진
    signup_ip = db.Column(db.String(64), default="", index=True)
    points = db.Column(db.Integer, default=0, nullable=False)
    is_admin = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    last_post_at = db.Column(db.DateTime)
    last_comment_at = db.Column(db.DateTime)
    # 로그인 실패 잠금 (비밀번호 무작위 대입 방지)
    failed_logins = db.Column(db.Integer, default=0, nullable=False)
    locked_until = db.Column(db.DateTime)

    def owns_pack(self, pack_id):
        return UserConPack.query.filter_by(user_id=self.id, pack_id=pack_id).first() is not None


class Community(db.Model):
    """커뮤니티 (게시판)"""
    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(30), unique=True, nullable=False)
    name = db.Column(db.String(30), unique=True, nullable=False)
    description = db.Column(db.String(200), default="")
    creator_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    icon = db.Column(db.String(500))        # 아이콘 이미지 (Blob URL 또는 static 경로)
    banner = db.Column(db.String(500))      # 상단 배너 이미지
    categories = db.Column(db.String(300), default="")  # 말머리 목록 (줄바꿈 구분)

    creator = db.relationship("User")
    posts = db.relationship("Post", back_populates="community", lazy="dynamic", cascade="all, delete-orphan")

    boards = db.relationship("Board", backref="community", cascade="all, delete-orphan",
                             order_by="Board.sort_order, Board.id", lazy="selectin")

    @property
    def category_list(self):
        return [c for c in (self.categories or "").split("\n") if c]

    def writable_boards(self, user):
        return [b for b in self.boards if not b.manager_only or self.is_manager(user)]

    def is_manager(self, user):
        return user is not None and (user.id == self.creator_id or user.is_admin)


class Post(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    community_id = db.Column(db.Integer, db.ForeignKey("community.id"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    title = db.Column(db.String(100), nullable=False)
    content = db.Column(db.Text, nullable=False)
    views = db.Column(db.Integer, default=0, nullable=False)
    upvotes = db.Column(db.Integer, default=0, nullable=False)
    downvotes = db.Column(db.Integer, default=0, nullable=False)
    comment_count = db.Column(db.Integer, default=0, nullable=False)
    points_awarded = db.Column(db.Integer, default=0, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)
    updated_at = db.Column(db.DateTime)
    category = db.Column(db.String(20))  # 말머리
    is_notice = db.Column(db.Boolean, default=False, nullable=False)  # 공지 (커뮤니티 관리자가 지정)
    has_image = db.Column(db.Boolean, default=False, nullable=False)
    has_poll = db.Column(db.Boolean, default=False, nullable=False)
    has_video = db.Column(db.Boolean, default=False, nullable=False)
    board_id = db.Column(db.Integer, db.ForeignKey("board.id"), index=True)     # 커뮤니티 안의 게시판
    series_id = db.Column(db.Integer, db.ForeignKey("series.id"), index=True)   # 시리즈
    fmt = db.Column(db.String(10), default="text", nullable=False)  # text(예전 글) / html(에디터 글)
    text = db.Column(db.Text)  # 검색·미리보기용 순수 글자

    poll = db.relationship("Poll", uselist=False, back_populates="post", cascade="all, delete-orphan")
    board = db.relationship("Board", lazy="joined")
    series = db.relationship("Series", lazy="joined")

    # 목록에서 글쓴이/커뮤니티 이름을 함께 가져와 쿼리 수를 줄인다
    author = db.relationship("User", lazy="joined")
    community = db.relationship("Community", back_populates="posts", lazy="joined")
    comments = db.relationship("Comment", backref="post", cascade="all, delete-orphan", order_by="Comment.id")
    votes = db.relationship("Vote", backref="post", cascade="all, delete-orphan")

    @property
    def score(self):
        return self.upvotes - self.downvotes


class Comment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(db.Integer, db.ForeignKey("post.id"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    content = db.Column(db.Text, default="")
    con_item_id = db.Column(db.Integer, db.ForeignKey("con_item.id"))
    points_awarded = db.Column(db.Integer, default=0, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    author = db.relationship("User", lazy="joined")
    con_item = db.relationship("ConItem", lazy="joined")


class Vote(db.Model):
    """추천(+1) / 비추천(-1). 실시간 베스트 집계를 위해 시각을 저장한다."""
    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(db.Integer, db.ForeignKey("post.id"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    value = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)

    __table_args__ = (db.UniqueConstraint("post_id", "user_id", name="uq_vote_post_user"),)


class ConPack(db.Model):
    """커뮤콘 묶음"""
    id = db.Column(db.Integer, primary_key=True)
    creator_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    name = db.Column(db.String(40), nullable=False)
    description = db.Column(db.String(300), default="")
    price = db.Column(db.Integer, default=0, nullable=False)
    sales_count = db.Column(db.Integer, default=0, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    creator = db.relationship("User", lazy="joined")
    items = db.relationship("ConItem", backref="pack", cascade="all, delete-orphan",
                            order_by="ConItem.sort_order", lazy="selectin")

    @property
    def thumbnail(self):
        return self.items[0] if self.items else None


class ConItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    pack_id = db.Column(db.Integer, db.ForeignKey("con_pack.id"), nullable=False, index=True)
    path = db.Column(db.String(500), nullable=False)  # Blob URL 또는 static 기준 상대 경로
    sort_order = db.Column(db.Integer, default=0, nullable=False)


class UserConPack(db.Model):
    """유저가 보유한 커뮤콘"""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    pack_id = db.Column(db.Integer, db.ForeignKey("con_pack.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    pack = db.relationship("ConPack", lazy="joined")

    __table_args__ = (db.UniqueConstraint("user_id", "pack_id", name="uq_user_pack"),)


class Board(db.Model):
    """커뮤니티 안의 게시판 (글 종류 나누기). 커뮤니티 관리자가 만든다."""
    id = db.Column(db.Integer, primary_key=True)
    community_id = db.Column(db.Integer, db.ForeignKey("community.id"), nullable=False, index=True)
    name = db.Column(db.String(20), nullable=False)
    sort_order = db.Column(db.Integer, default=0, nullable=False)
    manager_only = db.Column(db.Boolean, default=False, nullable=False)  # 관리자만 글쓰기 (예: 공지 게시판)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)


class Series(db.Model):
    """글 묶음 (연재). 글쓴이가 만들고 자기 글을 넣는다."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    title = db.Column(db.String(40), nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)


class LinkPreview(db.Model):
    """외부 콘텐츠 카드 (링크의 제목/설명/대표 이미지)"""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    url = db.Column(db.String(1000), nullable=False)
    title = db.Column(db.String(200), default="")
    description = db.Column(db.String(300), default="")
    image = db.Column(db.String(1000), default="")
    site = db.Column(db.String(100), default="")
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)


class Upload(db.Model):
    """올린 이미지 (글 사진 / 커뮤콘 / 프로필 / 커뮤니티 아이콘·배너)"""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    kind = db.Column(db.String(20), nullable=False)
    path = db.Column(db.String(500), nullable=False)
    width = db.Column(db.Integer)
    height = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)


class Poll(db.Model):
    """글에 붙는 투표"""
    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(db.Integer, db.ForeignKey("post.id"), nullable=False, unique=True)
    question = db.Column(db.String(100), nullable=False)
    multiple = db.Column(db.Boolean, default=False, nullable=False)  # 여러 개 선택 가능
    ends_at = db.Column(db.DateTime)  # None 이면 마감 없음
    voter_count = db.Column(db.Integer, default=0, nullable=False)

    post = db.relationship("Post", back_populates="poll")
    options = db.relationship("PollOption", backref="poll", cascade="all, delete-orphan",
                              order_by="PollOption.sort_order", lazy="selectin")
    votes = db.relationship("PollVote", cascade="all, delete-orphan")

    @property
    def closed(self):
        return self.ends_at is not None and self.ends_at <= utcnow()

    @property
    def total_votes(self):
        return sum(o.votes for o in self.options)


class PollOption(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    poll_id = db.Column(db.Integer, db.ForeignKey("poll.id"), nullable=False, index=True)
    text = db.Column(db.String(50), nullable=False)
    sort_order = db.Column(db.Integer, default=0, nullable=False)
    votes = db.Column(db.Integer, default=0, nullable=False)


class PollVote(db.Model):
    """한 사람당 투표 하나에 한 번만 (여러 개 선택이면 option_ids 에 여러 개)"""
    id = db.Column(db.Integer, primary_key=True)
    poll_id = db.Column(db.Integer, db.ForeignKey("poll.id"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    option_ids = db.Column(db.String(100), nullable=False)  # "3,5"
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    __table_args__ = (db.UniqueConstraint("poll_id", "user_id", name="uq_poll_vote"),)

    @property
    def option_set(self):
        return {int(x) for x in self.option_ids.split(",") if x}


class PointLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    amount = db.Column(db.Integer, nullable=False)
    kind = db.Column(db.String(20), nullable=False)  # post / comment / con_buy / revoke
    reason = db.Column(db.String(200), default="")
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)
