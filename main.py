"""홈 (전체 베스트) / 마이페이지 / 약관"""
from datetime import timedelta

from flask import Blueprint, Response, g, render_template, request

from models import Comment, Community, ConPack, PointLog, Post, UserConPack, db, utcnow
from utils import login_required, period_best, realtime_best

bp = Blueprint("main", __name__)


def hot_communities(limit=10):
    """흥한 커뮤니티: 최근 24시간 글 수 → 전체 글 수 순"""
    since = utcnow() - timedelta(days=1)
    recent = db.func.count(Post.id).filter(Post.created_at >= since)
    return (
        db.session.query(Community, recent.label("recent"), db.func.count(Post.id).label("total"))
        .outerjoin(Post, Post.community_id == Community.id)
        .group_by(Community.id)
        .order_by(recent.desc(), db.func.count(Post.id).desc(), Community.id)
        .limit(limit)
        .all()
    )


@bp.route("/")
def home():
    return render_template(
        "home.html",
        realtime=realtime_best(15),
        daily=period_best(1, 15),
        weekly=period_best(7, 15),
        hot=hot_communities(),
        new_cons=ConPack.query.order_by(ConPack.id.desc()).limit(6).all(),
        latest=Post.query.order_by(Post.id.desc()).limit(15).all(),
    )


@bp.route("/terms")
def terms():
    return render_template("terms.html")


@bp.route("/privacy")
def privacy():
    return render_template("privacy.html")


@bp.route("/robots.txt")
def robots():
    body = "User-agent: *\nDisallow: /me\nDisallow: /login\nDisallow: /signup\nDisallow: /forgot\n"
    return Response(body, mimetype="text/plain")


@bp.route("/me")
@login_required
def mypage():
    page = request.args.get("page", 1, type=int)
    logs = (PointLog.query.filter_by(user_id=g.user.id)
            .order_by(PointLog.id.desc()).paginate(page=page, per_page=20, error_out=False))
    packs = [up.pack for up in UserConPack.query.filter_by(user_id=g.user.id).order_by(UserConPack.id.desc())]
    stats = {
        "posts": Post.query.filter_by(user_id=g.user.id).count(),
        "comments": Comment.query.filter_by(user_id=g.user.id).count(),
        "community": Community.query.filter_by(creator_id=g.user.id).first(),
    }
    return render_template("mypage.html", logs=logs, packs=packs, stats=stats)
