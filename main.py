"""홈 (전체 베스트) / 마이페이지 / 약관"""
from datetime import timedelta

from flask import (Blueprint, Response, abort, current_app, flash, g, redirect, render_template, request,
                   send_from_directory, url_for)

from uploads import owned_upload

from models import Comment, Community, CommunityMember, ConPack, PointLog, Post, User, UserConPack, db, utcnow
from utils import login_required, period_best, realtime_best

bp = Blueprint("main", __name__)


def hot_communities(limit=10):
    """인기 커뮤니티: 최근 24시간 글 수 → 전체 글 수 순"""
    since = utcnow() - timedelta(days=1)
    recent = db.func.count(Post.id).filter(Post.created_at >= since)
    return (
        db.session.query(Community, recent.label("recent"), db.func.count(Post.id).label("total"))
        .outerjoin(Post, Post.community_id == Community.id)
        .filter(Community.status == "approved")
        .group_by(Community.id)
        .order_by(recent.desc(), db.func.count(Post.id).desc(), Community.id)
        .limit(limit)
        .all()
    )


def joined_communities(user, limit=None):
    """내가 가입한 (승인된) 커뮤니티"""
    if user is None:
        return []
    q = (Community.query.join(CommunityMember, CommunityMember.community_id == Community.id)
         .filter(CommunityMember.user_id == user.id, Community.status == "approved")
         .order_by(CommunityMember.id.desc()))
    return q.limit(limit).all() if limit else q.all()


@bp.route("/")
def home():
    return render_template(
        "home.html",
        realtime=realtime_best(15),
        daily=period_best(1, 15),
        weekly=period_best(7, 15),
        hot=hot_communities(),
        joined=joined_communities(g.user, 12),
        new_cons=ConPack.query.order_by(ConPack.id.desc()).limit(6).all(),
        latest=Post.query.order_by(Post.id.desc()).limit(15).all(),
    )


@bp.route("/terms")
def terms():
    return render_template("terms.html")


@bp.route("/privacy")
def privacy():
    return render_template("privacy.html")


@bp.route("/favicon.ico")
def favicon():
    # 브라우저와 검색엔진이 기본으로 찾는 주소
    return send_from_directory(current_app.static_folder, "img/favicon.ico", mimetype="image/x-icon",
                               max_age=86400)


@bp.route("/<string:name>.html")
def site_verification(name):
    """검색엔진 소유 확인 파일 (구글: google로 시작하는 파일). 등록된 이름만 응답한다."""
    if name not in current_app.config["SITE_VERIFICATION_FILES"]:
        return render_template("error.html", code=404, message="페이지를 찾을 수 없습니다."), 404
    if name.startswith("google"):
        body = f"google-site-verification: {name}.html"
    else:
        body = name
    return Response(body, mimetype="text/html")


@bp.route("/robots.txt")
def robots():
    site = current_app.config["SITE_URL"]
    body = "\n".join([
        "User-agent: *",
        "Allow: /",
        "Disallow: /me", "Disallow: /login", "Disallow: /signup", "Disallow: /forgot", "Disallow: /welcome",
        "Disallow: /admin/", "Disallow: /upload", "Disallow: /captcha.png", "Disallow: /search",
        "Disallow: /c/*/write", "Disallow: /c/*/edit", "Disallow: /c/*/manage", "Disallow: /u/",
        "",
        f"Sitemap: {site}/sitemap.xml",
        "",
    ])
    resp = Response(body, mimetype="text/plain")
    resp.headers["Cache-Control"] = "public, max-age=3600"
    return resp


@bp.route("/sitemap.xml")
def sitemap():
    """구글/네이버가 사이트의 페이지를 빠짐없이 찾도록 알려주는 목록"""
    from xml.sax.saxutils import escape as xml_escape
    site = current_app.config["SITE_URL"]

    def day(dt):
        return dt.strftime("%Y-%m-%d") if dt else None

    urls = [(f"{site}/", day(utcnow()), "daily", "1.0"), (f"{site}/c", None, "daily", "0.8"),
            (f"{site}/con/", None, "weekly", "0.5"), (f"{site}/terms", None, "yearly", "0.1"),
            (f"{site}/privacy", None, "yearly", "0.1")]
    latest = dict(db.session.query(Post.community_id, db.func.max(Post.created_at)).group_by(Post.community_id).all())
    for c in Community.query.filter_by(status="approved").order_by(Community.id):
        urls.append((f"{site}/c/{c.slug}", day(latest.get(c.id) or c.created_at), "hourly", "0.9"))
    posts = (db.session.query(Post.id, Post.created_at, Post.updated_at, Community.slug)
             .join(Community, Community.id == Post.community_id)
             .filter(Community.status == "approved")
             .order_by(Post.id.desc()).limit(5000).all())
    for pid, created, updated, slug in posts:
        urls.append((f"{site}/c/{slug}/{pid}", day(updated or created), None, "0.6"))

    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for loc, lastmod, freq, prio in urls:
        lines.append("  <url><loc>" + xml_escape(loc) + "</loc>"
                     + (f"<lastmod>{lastmod}</lastmod>" if lastmod else "")
                     + (f"<changefreq>{freq}</changefreq>" if freq else "")
                     + f"<priority>{prio}</priority></url>")
    lines.append("</urlset>")
    resp = Response("\n".join(lines), mimetype="application/xml")
    resp.headers["Cache-Control"] = "public, max-age=1800"
    return resp


@bp.route("/u/<int:user_id>")
def profile(user_id):
    """회원 프로필: 쓴 글 / 댓글 (비공개로 할 수 있음)"""
    user = db.session.get(User, user_id) or abort(404)
    is_self = g.user is not None and g.user.id == user.id
    if user.is_admin and not is_self:
        abort(404)  # 사이트 관리자 계정은 존재 자체를 드러내지 않는다
    tab = "comments" if request.args.get("tab") == "comments" else "posts"
    page = request.args.get("page", 1, type=int)
    hidden = None
    if user.username is None:
        hidden = "탈퇴한 회원이에요."
    elif user.profile_private and not is_self and not (g.user and g.user.is_admin):
        hidden = "비공개 계정이에요. 이 회원의 활동은 볼 수 없어요."
    pagination = None
    if not hidden:
        if tab == "posts":
            q = (Post.query.join(Community, Community.id == Post.community_id)
                 .filter(Post.user_id == user.id, Community.status == "approved").order_by(Post.id.desc()))
        else:
            q = (Comment.query.join(Post, Post.id == Comment.post_id)
                 .join(Community, Community.id == Post.community_id)
                 .filter(Comment.user_id == user.id, Community.status == "approved").order_by(Comment.id.desc()))
        pagination = q.paginate(page=page, per_page=20, error_out=False)
    counts = {"posts": Post.query.filter_by(user_id=user.id).count(),
              "comments": Comment.query.filter_by(user_id=user.id).count()}
    return render_template("profile.html", person=user, tab=tab, pagination=pagination, hidden=hidden,
                           counts=counts, is_self=is_self)


@bp.route("/me/privacy", methods=["POST"])
@login_required
def set_privacy():
    g.user.profile_private = request.form.get("private") == "1"
    db.session.commit()
    flash("활동을 비공개로 바꿨어요. 다른 사람은 내 글·댓글 목록을 볼 수 없어요." if g.user.profile_private
          else "활동을 공개로 바꿨어요. 다른 사람이 내 프로필에서 글·댓글을 볼 수 있어요.", "info")
    return redirect(url_for("main.mypage"))


@bp.route("/me/avatar", methods=["POST"])
@login_required
def set_avatar():
    if request.form.get("remove"):
        g.user.avatar = None
        flash("프로필 사진을 지웠어요.", "info")
    else:
        up = owned_upload(request.form.get("avatar_id", type=int), g.user, "avatar")
        if not up:
            flash("사진을 먼저 올려주세요.", "error")
            return redirect(url_for("main.mypage"))
        g.user.avatar = up.path
        flash("프로필 사진을 바꿨어요.", "success")
    db.session.commit()
    return redirect(url_for("main.mypage"))


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
    return render_template("mypage.html", logs=logs, packs=packs, stats=stats, joined=joined_communities(g.user))
