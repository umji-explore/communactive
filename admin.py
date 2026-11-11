"""사이트 관리자 페이지: 커뮤니티 개설 승인 / 커뮤니티 · 커뮤콘 관리

관리자가 아닌 사람에게는 이 주소가 있다는 것조차 보이지 않게 404 를 돌려준다.
"""
from functools import wraps

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

import notify
from models import Community, ConPack, Post, User, db

bp = Blueprint("admin", __name__, url_prefix="/admin")


def admin_only(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if g.user is None or not g.user.is_admin:
            abort(404)
        return view(*args, **kwargs)
    return wrapper


@bp.route("/")
@admin_only
def index():
    pending = Community.query.filter_by(status="pending").order_by(Community.id).all()
    communities = (db.session.query(Community, db.func.count(Post.id))
                   .outerjoin(Post, Post.community_id == Community.id)
                   .filter(Community.status == "approved").group_by(Community.id)
                   .order_by(Community.id.desc()).all())
    cons = ConPack.query.order_by(ConPack.id.desc()).limit(100).all()
    stats = {"users": User.query.filter(User.username.isnot(None)).count(), "posts": Post.query.count()}
    return render_template("admin.html", pending=pending, communities=communities, cons=cons, stats=stats)


@bp.route("/community/<int:cid>", methods=["POST"])
@admin_only
def decide_community(cid):
    c = db.session.get(Community, cid) or abort(404)
    if request.form.get("decision") == "approve":
        c.status = "approved"
        notify.on_community_approved(c)
        db.session.commit()
        flash(f"'{c.name}' 커뮤니티를 승인했어요.", "success")
    elif c.status == "pending":
        name = c.name
        db.session.delete(c)  # 거절하면 지워서 이름/주소를 다시 쓸 수 있게
        db.session.commit()
        flash(f"'{name}' 커뮤니티 개설을 거절했어요.", "info")
    return redirect(url_for("admin.index"))
