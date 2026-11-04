"""커뮤니티 (게시판) / 게시글 / 댓글 / 추천 / 검색"""
import re

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, session, url_for
from sqlalchemy.exc import IntegrityError

from models import Comment, Community, ConItem, Post, UserConPack, Vote, db, utcnow
from utils import add_points, community_popular, cooldown_left, login_required, popular_filter, reward_amount

bp = Blueprint("board", __name__)
SLUG_RE = re.compile(r"^[a-z0-9_]{2,20}$")
RESERVED_SLUGS = {"new", "admin", "api", "static"}


def get_community(slug):
    return Community.query.filter_by(slug=slug).first_or_404()


def get_post(community, post_id):
    post = db.session.get(Post, post_id)
    if not post or post.community_id != community.id:
        abort(404)
    return post


def can_manage(obj):
    return g.user is not None and (obj.user_id == g.user.id or g.user.is_admin)


def validate_post(title, content):
    if not 1 <= len(title) <= 100:
        return "제목은 1~100자로 입력해주세요."
    if not 1 <= len(content) <= 20000:
        return "내용은 1~20,000자로 입력해주세요."
    return None


# ---------- 커뮤니티 ----------

@bp.route("/c")
def communi_list():
    q = request.args.get("q", "").strip()
    rows = (
        db.session.query(Community, db.func.count(Post.id))
        .outerjoin(Post, Post.community_id == Community.id)
        .group_by(Community.id)
    )
    if q:
        rows = rows.filter(Community.name.contains(q) | Community.description.contains(q))
    rows = rows.order_by(db.func.count(Post.id).desc(), Community.name).all()
    return render_template("communi_list.html", rows=rows, q=q)


@bp.route("/c/new", methods=["GET", "POST"])
@login_required
def communi_new():
    # 커뮤니티는 한 사람당 하나만 만들 수 있다 (관리자는 운영용으로 예외)
    mine = Community.query.filter_by(creator_id=g.user.id).first()
    if mine and not g.user.is_admin:
        flash(f"커뮤니티는 한 명당 하나만 만들 수 있어요. 이미 '{mine.name}' 커뮤니티를 운영 중입니다.", "error")
        return redirect(url_for("board.communi", slug=mine.slug))

    form = {"name": "", "slug": "", "description": ""}
    if request.method == "POST":
        form = {k: request.form.get(k, "").strip() for k in form}
        form["slug"] = form["slug"].lower()
        error = None
        if not 2 <= len(form["name"]) <= 20:
            error = "커뮤니티 이름은 2~20자로 입력해주세요."
        elif not SLUG_RE.match(form["slug"]):
            error = "주소는 2~20자의 영문 소문자, 숫자, _ 만 쓸 수 있습니다."
        elif form["slug"] in RESERVED_SLUGS:
            error = "사용할 수 없는 주소입니다."
        elif len(form["description"]) > 200:
            error = "소개는 200자 이하로 입력해주세요."
        elif Community.query.filter((Community.slug == form["slug"]) | (Community.name == form["name"])).first():
            error = "이미 존재하는 커뮤니티 이름 또는 주소입니다."
        if not error:
            c = Community(creator_id=g.user.id, **form)
            db.session.add(c)
            try:
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                error = "이미 존재하는 커뮤니티 이름 또는 주소입니다."
            else:
                flash(f"'{c.name}' 커뮤니티가 만들어졌습니다!", "success")
                return redirect(url_for("board.communi", slug=c.slug))
        flash(error, "error")
    return render_template("communi_new.html", form=form)


@bp.route("/c/<slug>")
def communi(slug):
    community = get_community(slug)
    mode = request.args.get("mode", "all")
    page = request.args.get("page", 1, type=int)
    q = request.args.get("q", "").strip()

    query = Post.query.filter_by(community_id=community.id)
    if mode == "best":
        query = popular_filter(query)
    if q:
        query = query.filter(Post.title.contains(q) | Post.content.contains(q))
    pagination = query.order_by(Post.id.desc()).paginate(
        page=page, per_page=current_app.config["POSTS_PER_PAGE"], error_out=False
    )
    return render_template(
        "communi.html", community=community, popular=community_popular(community),
        pagination=pagination, mode=mode, q=q,
    )


@bp.route("/search")
def search():
    q = request.args.get("q", "").strip()
    page = request.args.get("page", 1, type=int)
    pagination = None
    communities = []
    if len(q) >= 2:
        pagination = (
            Post.query.filter(Post.title.contains(q) | Post.content.contains(q))
            .order_by(Post.id.desc())
            .paginate(page=page, per_page=current_app.config["POSTS_PER_PAGE"], error_out=False)
        )
        communities = Community.query.filter(Community.name.contains(q)).limit(10).all()
    return render_template("search.html", q=q, pagination=pagination, communities=communities)


# ---------- 글 ----------

@bp.route("/c/<slug>/write", methods=["GET", "POST"])
@login_required
def write(slug):
    community = get_community(slug)
    title, content = "", ""
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        content = request.form.get("content", "").strip()
        wait = cooldown_left(g.user.last_post_at, current_app.config["POST_COOLDOWN"])
        error = validate_post(title, content) or (f"도배 방지: {wait}초 후에 다시 작성할 수 있습니다." if wait else None)
        if error:
            flash(error, "error")
        else:
            reward = reward_amount(g.user, "post")
            post = Post(community_id=community.id, user_id=g.user.id, title=title, content=content,
                        points_awarded=reward)
            db.session.add(post)
            g.user.last_post_at = utcnow()
            if reward:
                add_points(g.user, reward, "post", f"글 작성: {title}")
            db.session.commit()
            flash(f"글이 등록되었습니다. +{reward}P" if reward else "글이 등록되었습니다. (오늘 글 포인트 한도 도달)", "success")
            return redirect(url_for("board.post", slug=slug, post_id=post.id))
    return render_template("write.html", community=community, title=title, content=content, post=None)


@bp.route("/c/<slug>/<int:post_id>/edit", methods=["GET", "POST"])
@login_required
def edit(slug, post_id):
    community = get_community(slug)
    p = get_post(community, post_id)
    if not can_manage(p):
        abort(403)
    title, content = p.title, p.content
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        content = request.form.get("content", "").strip()
        error = validate_post(title, content)
        if error:
            flash(error, "error")
        else:
            p.title, p.content, p.updated_at = title, content, utcnow()
            db.session.commit()
            flash("글이 수정되었습니다.", "success")
            return redirect(url_for("board.post", slug=slug, post_id=p.id))
    return render_template("write.html", community=community, title=title, content=content, post=p)


@bp.route("/c/<slug>/<int:post_id>")
def post(slug, post_id):
    community = get_community(slug)
    p = get_post(community, post_id)

    viewed = session.get("viewed", [])
    if p.id not in viewed:
        p.views = Post.views + 1
        db.session.commit()
        session["viewed"] = (viewed + [p.id])[-200:]

    my_vote = None
    my_packs = []
    if g.user:
        v = Vote.query.filter_by(post_id=p.id, user_id=g.user.id).first()
        my_vote = v.value if v else None
        my_packs = [up.pack for up in UserConPack.query.filter_by(user_id=g.user.id).order_by(UserConPack.id)]
    return render_template(
        "post.html", community=community, post=p, my_vote=my_vote, my_packs=my_packs,
        popular=community_popular(community), can_manage=can_manage(p),
    )


@bp.route("/c/<slug>/<int:post_id>/delete", methods=["POST"])
@login_required
def delete_post(slug, post_id):
    community = get_community(slug)
    p = get_post(community, post_id)
    if not can_manage(p):
        abort(403)
    # 글 작성 포인트만 회수 (댓글 단 사람들의 포인트는 회수하지 않음)
    revoked = p.points_awarded
    if revoked:
        add_points(p.author, -revoked, "revoke", f"글 삭제: {p.title}")
    db.session.delete(p)
    db.session.commit()
    flash("글이 삭제되었습니다." + (f" (-{revoked}P)" if revoked else ""), "info")
    return redirect(url_for("board.communi", slug=slug))


@bp.route("/c/<slug>/<int:post_id>/vote", methods=["POST"])
@login_required
def vote(slug, post_id):
    community = get_community(slug)
    p = get_post(community, post_id)
    value = 1 if request.form.get("value") == "up" else -1
    if p.user_id == g.user.id:
        flash("자신의 글에는 추천/비추천할 수 없습니다.", "error")
    else:
        try:
            db.session.add(Vote(post_id=p.id, user_id=g.user.id, value=value))
            db.session.flush()  # 중복이면 여기서 IntegrityError
            if value == 1:
                p.upvotes = Post.upvotes + 1
            else:
                p.downvotes = Post.downvotes + 1
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("이미 참여했습니다.", "error")
    return redirect(url_for("board.post", slug=slug, post_id=post_id) + "#vote")


# ---------- 댓글 ----------

@bp.route("/c/<slug>/<int:post_id>/comment", methods=["POST"])
@login_required
def comment(slug, post_id):
    community = get_community(slug)
    p = get_post(community, post_id)
    content = request.form.get("content", "").strip()
    con_id = request.form.get("con_id", type=int)
    back = url_for("board.post", slug=slug, post_id=p.id) + "#comments"

    con = None
    if con_id:
        con = db.session.get(ConItem, con_id)
        if not con or not g.user.owns_pack(con.pack_id):
            flash("보유하지 않은 커뮤콘입니다.", "error")
            return redirect(back)
    wait = cooldown_left(g.user.last_comment_at, current_app.config["COMMENT_COOLDOWN"])
    if not content and not con:
        flash("댓글 내용을 입력하거나 커뮤콘을 선택해주세요.", "error")
    elif len(content) > 1000:
        flash("댓글은 1,000자 이하로 입력해주세요.", "error")
    elif wait:
        flash(f"도배 방지: {wait}초 후에 다시 작성할 수 있습니다.", "error")
    else:
        reward = reward_amount(g.user, "comment")
        c = Comment(post_id=p.id, user_id=g.user.id, content=content,
                    con_item_id=con.id if con else None, points_awarded=reward)
        db.session.add(c)
        p.comment_count = Post.comment_count + 1
        g.user.last_comment_at = utcnow()
        if reward:
            add_points(g.user, reward, "comment", f"댓글 작성: {p.title}")
        db.session.commit()
        if reward:
            flash(f"댓글이 등록되었습니다. +{reward}P", "success")
        return redirect(url_for("board.post", slug=slug, post_id=p.id) + f"#c{c.id}")
    return redirect(back)


@bp.route("/comment/<int:comment_id>/delete", methods=["POST"])
@login_required
def delete_comment(comment_id):
    c = db.session.get(Comment, comment_id) or abort(404)
    if not can_manage(c):
        abort(403)
    p = c.post
    slug, post_id = p.community.slug, p.id
    if c.points_awarded:
        add_points(c.author, -c.points_awarded, "revoke", f"댓글 삭제: {p.title}")
    p.comment_count = Post.comment_count - 1
    db.session.delete(c)
    db.session.commit()
    return redirect(url_for("board.post", slug=slug, post_id=post_id) + "#comments")
