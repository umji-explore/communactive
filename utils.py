import hmac
import re
import secrets
from datetime import timedelta
from functools import wraps

from flask import abort, current_app, flash, g, redirect, request, session, url_for
from markupsafe import Markup, escape
from sqlalchemy import func

from models import PointLog, Post, Vote, db, utcnow

KST = timedelta(hours=9)
NICKNAME_RE = re.compile(r"^[가-힣a-zA-Z0-9_]{2,12}$")


# ---------- 로그인 ----------

def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if g.user is None:
            flash("로그인이 필요합니다.", "error")
            return redirect(url_for("auth.login", next=request.full_path))
        return view(*args, **kwargs)
    return wrapper


def safe_next(target, fallback="/"):
    if target and target.startswith("/") and not target.startswith("//"):
        return target
    return fallback


# ---------- CSRF ----------

def csrf_token():
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_urlsafe(32)
    return session["_csrf"]


def check_csrf():
    if request.method != "POST":
        return
    expected = session.get("_csrf")
    sent = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
    if not expected or not sent or not hmac.compare_digest(expected, sent):
        abort(400, "잘못된 요청입니다. 페이지를 새로고침 후 다시 시도해주세요.")


# ---------- 포인트 ----------

def add_points(user, amount, kind, reason=""):
    user.points += amount
    db.session.add(PointLog(user_id=user.id, amount=amount, kind=kind, reason=reason[:200]))


def earned_today(user, kind):
    """오늘(KST) 해당 종류로 포인트를 받은 횟수"""
    now_kst = utcnow() + KST
    start_utc = now_kst.replace(hour=0, minute=0, second=0, microsecond=0) - KST
    return PointLog.query.filter(
        PointLog.user_id == user.id, PointLog.kind == kind,
        PointLog.amount > 0, PointLog.created_at >= start_utc,
    ).count()


def reward_amount(user, kind):
    """이번 활동으로 지급할 포인트 (일일 한도 초과 시 0)"""
    cfg = current_app.config
    amount = cfg["POINTS_POST"] if kind == "post" else cfg["POINTS_COMMENT"]
    limit = cfg["DAILY_POST_POINT_LIMIT"] if kind == "post" else cfg["DAILY_COMMENT_POINT_LIMIT"]
    if limit and earned_today(user, kind) >= limit:
        return 0
    return amount


def cooldown_left(last_at, seconds):
    if not last_at or not seconds:
        return 0
    left = seconds - (utcnow() - last_at).total_seconds()
    return max(0, int(left) + 1) if left > 0 else 0


# ---------- 베스트 / 인기글 ----------

def realtime_best(limit=10):
    """최근 N시간 동안 받은 추천 수 기준"""
    since = utcnow() - timedelta(hours=current_app.config["REALTIME_HOURS"])
    recent = (
        db.session.query(Vote.post_id, func.sum(Vote.value).label("recent"))
        .filter(Vote.created_at >= since)
        .group_by(Vote.post_id)
        .subquery()
    )
    rows = (
        db.session.query(Post)
        .join(recent, Post.id == recent.c.post_id)
        .filter(recent.c.recent > 0)
        .order_by(recent.c.recent.desc(), (Post.upvotes - Post.downvotes).desc(), Post.id.desc())
        .limit(limit)
        .all()
    )
    return rows


def period_best(days, limit=10):
    since = utcnow() - timedelta(days=days)
    return (
        Post.query.filter(Post.created_at >= since, Post.upvotes - Post.downvotes > 0)
        .order_by((Post.upvotes - Post.downvotes).desc(), Post.comment_count.desc(), Post.id.desc())
        .limit(limit)
        .all()
    )


def popular_filter(query):
    return query.filter(Post.upvotes >= current_app.config["POPULAR_MIN_UPVOTES"])


def community_popular(community, limit=5):
    """커뮤니 상단 인기글: 최근 7일 내 추천 기준 이상 글"""
    since = utcnow() - timedelta(days=7)
    q = popular_filter(Post.query.filter(Post.community_id == community.id, Post.created_at >= since))
    return q.order_by(Post.upvotes.desc(), Post.id.desc()).limit(limit).all()


# ---------- 템플릿 필터 ----------

def kst(dt):
    return dt + KST if dt else dt


def short_time(dt):
    """목록용: 오늘이면 HH:MM, 아니면 MM.DD"""
    if not dt:
        return ""
    local = kst(dt)
    today = kst(utcnow()).date()
    return local.strftime("%H:%M") if local.date() == today else local.strftime("%m.%d")


def full_time(dt):
    return kst(dt).strftime("%Y.%m.%d %H:%M") if dt else ""


def nl2br(text):
    return Markup("<br>".join(escape(text or "").split("\n")))


def register_template_helpers(app):
    app.jinja_env.filters["short_time"] = short_time
    app.jinja_env.filters["full_time"] = full_time
    app.jinja_env.filters["nl2br"] = nl2br
    app.jinja_env.filters["comma"] = lambda n: f"{n:,}"
    app.jinja_env.globals["csrf_token"] = csrf_token
