"""홈 (전체 베스트) / 마이페이지"""
from flask import Blueprint, Response, g, render_template, request

from auth import PROVIDERS, enabled_providers
from models import Comment, Community, ConPack, PointLog, Post, UserConPack, db
from utils import login_required, period_best, realtime_best

bp = Blueprint("main", __name__)


@bp.route("/")
def home():
    communities = (
        db.session.query(Community, db.func.count(Post.id))
        .outerjoin(Post, Post.community_id == Community.id)
        .group_by(Community.id)
        .order_by(db.func.count(Post.id).desc())
        .limit(12)
        .all()
    )
    return render_template(
        "home.html",
        realtime=realtime_best(),
        daily=period_best(1),
        weekly=period_best(7),
        communities=communities,
        new_cons=ConPack.query.order_by(ConPack.id.desc()).limit(6).all(),
        latest=Post.query.order_by(Post.id.desc()).limit(10).all(),
    )


@bp.route("/terms")
def terms():
    return render_template("terms.html")


@bp.route("/privacy")
def privacy():
    return render_template("privacy.html")


@bp.route("/robots.txt")
def robots():
    body = "User-agent: *\nDisallow: /me\nDisallow: /login\nDisallow: /signup\nDisallow: /auth/\n"
    return Response(body, mimetype="text/plain")


@bp.route("/me")
@login_required
def mypage():
    page = request.args.get("page", 1, type=int)
    logs = (PointLog.query.filter_by(user_id=g.user.id)
            .order_by(PointLog.id.desc()).paginate(page=page, per_page=20, error_out=False))
    packs = [up.pack for up in UserConPack.query.filter_by(user_id=g.user.id).order_by(UserConPack.id.desc())]
    linked = {a.provider: a for a in g.user.social_accounts}
    stats = {
        "posts": Post.query.filter_by(user_id=g.user.id).count(),
        "comments": Comment.query.filter_by(user_id=g.user.id).count(),
    }
    return render_template(
        "mypage.html", logs=logs, packs=packs, linked=linked, providers=PROVIDERS,
        enabled=enabled_providers(), stats=stats,
    )
