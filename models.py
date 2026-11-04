from datetime import datetime, timezone

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


def utcnow():
    """DB에는 timezone 없는 UTC 시각으로 저장한다."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nickname = db.Column(db.String(20), unique=True, nullable=False)
    email = db.Column(db.String(255))
    points = db.Column(db.Integer, default=0, nullable=False)
    is_admin = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    last_post_at = db.Column(db.DateTime)
    last_comment_at = db.Column(db.DateTime)

    social_accounts = db.relationship("SocialAccount", backref="user", cascade="all, delete-orphan")

    def owns_pack(self, pack_id):
        return UserConPack.query.filter_by(user_id=self.id, pack_id=pack_id).first() is not None


class SocialAccount(db.Model):
    """구글/네이버/페이스북 계정 연동. 한 유저가 여러 계정을 연동할 수 있다."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    provider = db.Column(db.String(20), nullable=False)  # google / naver / facebook / dev
    provider_uid = db.Column(db.String(255), nullable=False)
    email = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    __table_args__ = (
        db.UniqueConstraint("provider", "provider_uid", name="uq_social_provider_uid"),
        db.UniqueConstraint("user_id", "provider", name="uq_social_user_provider"),
    )


class Community(db.Model):
    """커뮤니 (게시판)"""
    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(30), unique=True, nullable=False)
    name = db.Column(db.String(30), unique=True, nullable=False)
    description = db.Column(db.String(200), default="")
    creator_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    creator = db.relationship("User")
    posts = db.relationship("Post", backref="community", lazy="dynamic", cascade="all, delete-orphan")


class Post(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    community_id = db.Column(db.Integer, db.ForeignKey("community.id"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    title = db.Column(db.String(100), nullable=False)
    content = db.Column(db.Text, nullable=False)
    views = db.Column(db.Integer, default=0, nullable=False)
    upvotes = db.Column(db.Integer, default=0, nullable=False)
    downvotes = db.Column(db.Integer, default=0, nullable=False)
    comment_count = db.Column(db.Integer, default=0, nullable=False)
    points_awarded = db.Column(db.Integer, default=0, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)

    author = db.relationship("User")
    comments = db.relationship("Comment", backref="post", cascade="all, delete-orphan", order_by="Comment.id")
    votes = db.relationship("Vote", backref="post", cascade="all, delete-orphan")

    @property
    def score(self):
        return self.upvotes - self.downvotes


class Comment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(db.Integer, db.ForeignKey("post.id"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    content = db.Column(db.Text, default="")
    con_item_id = db.Column(db.Integer, db.ForeignKey("con_item.id"))
    points_awarded = db.Column(db.Integer, default=0, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    author = db.relationship("User")
    con_item = db.relationship("ConItem")


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

    creator = db.relationship("User")
    items = db.relationship("ConItem", backref="pack", cascade="all, delete-orphan", order_by="ConItem.sort_order")

    @property
    def thumbnail(self):
        return self.items[0] if self.items else None


class ConItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    pack_id = db.Column(db.Integer, db.ForeignKey("con_pack.id"), nullable=False, index=True)
    path = db.Column(db.String(255), nullable=False)  # static 기준 상대 경로
    sort_order = db.Column(db.Integer, default=0, nullable=False)


class UserConPack(db.Model):
    """유저가 보유한 커뮤콘"""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    pack_id = db.Column(db.Integer, db.ForeignKey("con_pack.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    pack = db.relationship("ConPack")

    __table_args__ = (db.UniqueConstraint("user_id", "pack_id", name="uq_user_pack"),)


class PointLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    amount = db.Column(db.Integer, nullable=False)
    kind = db.Column(db.String(20), nullable=False)  # post / comment / con_buy / revoke
    reason = db.Column(db.String(200), default="")
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)
