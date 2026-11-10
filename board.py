"""커뮤니티 / 게시판 / 게시글 / 댓글 / 추천 / 투표 / 시리즈 / 검색 / 커뮤니티 관리"""
import re
from datetime import timedelta

from flask import (Blueprint, abort, current_app, flash, g, jsonify, redirect, render_template, request, session,
                   url_for)
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

import content as content_mod
from models import (Board, Comment, Community, ConItem, Poll, PollOption, PollVote, Post, Series, User, UserConPack,
                    Vote, db, utcnow)
from uploads import owned_upload
from utils import (add_points, community_popular, cooldown_left, image_token_ids, login_required, popular_filter,
                   render_body, reward_amount)

bp = Blueprint("board", __name__)
SLUG_RE = re.compile(r"^[a-z0-9_]{2,20}$")
RESERVED_SLUGS = {"new", "admin", "api", "static"}
POLL_DAYS = {0: "마감 없음", 1: "1일", 3: "3일", 7: "7일"}
MAX_BOARDS = 20


def get_community(slug):
    return Community.query.filter_by(slug=slug).first_or_404()


def get_post(community, post_id):
    post = db.session.get(Post, post_id)
    if not post or post.community_id != community.id:
        abort(404)
    return post


def can_edit(obj):
    """수정: 쓴 사람 또는 사이트 관리자"""
    return g.user is not None and (obj.user_id == g.user.id or g.user.is_admin)


def can_delete(obj, community):
    """삭제: 쓴 사람, 사이트 관리자, 커뮤니티 관리자"""
    return can_edit(obj) or community.is_manager(g.user)


def validate_title(title):
    return None if 1 <= len(title) <= 100 else "제목은 1~100자로 입력해주세요."


def parse_poll(form):
    """글쓰기 폼의 투표 → (dict 또는 None, 오류)"""
    if not form.get("poll_on"):
        return None, None
    question = form.get("poll_question", "").strip()
    options = [o.strip() for o in form.getlist("poll_option") if o.strip()]
    days = form.get("poll_days", 0, type=int)
    if not 1 <= len(question) <= 100:
        return None, "투표 질문을 1~100자로 입력해주세요."
    if not 2 <= len(options) <= 10:
        return None, "투표 항목은 2~10개 넣어주세요."
    if any(len(o) > 50 for o in options):
        return None, "투표 항목은 50자 이하로 입력해주세요."
    if len(set(options)) != len(options):
        return None, "같은 투표 항목이 있어요."
    if days not in POLL_DAYS:
        days = 0
    return {"question": question, "options": options, "multiple": bool(form.get("poll_multi")), "days": days}, None


def pick_board(community, board_id, keep_id=None):
    """글을 넣을 게시판 확인. (Board 또는 None, 오류)
    keep_id: 수정할 때 원래 있던 게시판 (관리자 전용이어도 그대로 두는 건 허용)"""
    boards = community.boards
    if not boards:
        return None, None
    b = next((x for x in boards if x.id == board_id), None)
    if not b:
        return None, "게시판을 선택해주세요."
    if b.manager_only and not community.is_manager(g.user) and b.id != keep_id:
        return None, f"'{b.name}' 게시판은 관리자만 글을 쓸 수 있어요."
    return b, None


def pick_series(series_id, user_id):
    if not series_id:
        return None
    s = db.session.get(Series, series_id)
    return s if s and s.user_id == user_id else None


def editor_context(community, post=None):
    my_packs, my_series = [], []
    if g.user:
        my_packs = [up.pack for up in UserConPack.query.filter_by(user_id=g.user.id).order_by(UserConPack.id)]
        my_series = Series.query.filter_by(user_id=(post.user_id if post else g.user.id)).order_by(Series.id.desc()).all()
    return {
        "community": community, "post": post, "poll_days": POLL_DAYS, "my_packs": my_packs, "my_series": my_series,
        "boards": community.writable_boards(g.user),
        "ai_cost": current_app.config["AI_IMAGE_COST"], "ai_daily": current_app.config["AI_IMAGE_DAILY"],
        "video_max_mb": current_app.config["VIDEO_MAX_BYTES"] // (1024 * 1024),
    }


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
                flash(f"'{c.name}' 커뮤니티가 만들어졌습니다! 관리 메뉴에서 게시판, 아이콘, 배너를 꾸며보세요.", "success")
                return redirect(url_for("board.communi", slug=c.slug))
        flash(error, "error")
    return render_template("communi_new.html", form=form)


def save_boards(community, form):
    """관리 화면의 게시판 목록 저장. 화면에서 빠진 게시판은 삭제(글은 '게시판 없음'으로)."""
    ids = form.getlist("board_id")
    names = [n.strip() for n in form.getlist("board_name")]
    perms = form.getlist("board_perm")
    if not (len(ids) == len(names) == len(perms)):
        return "잘못된 요청입니다."
    rows = [(i, n, p) for i, n, p in zip(ids, names, perms) if n or i]
    if len(rows) > MAX_BOARDS:
        return f"게시판은 {MAX_BOARDS}개까지 만들 수 있어요."
    if any(not 1 <= len(n) <= 20 for _, n, _ in rows):
        return "게시판 이름은 1~20자로 정해주세요."
    if len({n for _, n, _ in rows}) != len(rows):
        return "같은 이름의 게시판이 있어요."
    existing = {str(b.id): b for b in community.boards}
    keep = set()
    for order, (bid, name, perm) in enumerate(rows):
        b = existing.get(bid)
        if not b:
            b = Board(community=community)
            db.session.add(b)
        b.name, b.sort_order, b.manager_only = name, order, perm == "manager"
        if bid:
            keep.add(bid)
    for bid, b in existing.items():
        if bid not in keep:
            Post.query.filter_by(board_id=b.id).update({"board_id": None})
            db.session.delete(b)
    return None


@bp.route("/c/<slug>/manage", methods=["GET", "POST"])
@login_required
def manage(slug):
    community = get_community(slug)
    if not community.is_manager(g.user):
        abort(403)
    if request.method == "POST":
        description = request.form.get("description", "").strip()
        error = "소개는 200자 이하로 입력해주세요." if len(description) > 200 else save_boards(community, request.form)
        if error:
            db.session.rollback()
            flash(error, "error")
        else:
            community.description = description
            for field in ("icon", "banner"):
                if request.form.get(f"remove_{field}"):
                    setattr(community, field, None)
                up = owned_upload(request.form.get(f"{field}_id", type=int), g.user, field)
                if up:
                    setattr(community, field, up.path)
            db.session.commit()
            flash("커뮤니티 설정이 저장되었습니다.", "success")
            return redirect(url_for("board.communi", slug=slug))
    return render_template("communi_manage.html", community=community)


@bp.route("/c/<slug>")
def communi(slug):
    community = get_community(slug)
    mode = request.args.get("mode", "all")
    page = request.args.get("page", 1, type=int)
    q = request.args.get("q", "").strip()
    board_id = request.args.get("board", type=int)
    board = next((b for b in community.boards if b.id == board_id), None)

    query = Post.query.filter_by(community_id=community.id)
    if mode == "best":
        query = popular_filter(query)
    elif mode == "notice":
        query = query.filter(Post.is_notice.is_(True))
    if board:
        query = query.filter(Post.board_id == board.id)
    if q:
        query = query.filter(Post.title.contains(q) | Post.text.contains(q))
    pagination = query.order_by(Post.id.desc()).paginate(
        page=page, per_page=current_app.config["POSTS_PER_PAGE"], error_out=False
    )
    notices = []
    if mode == "all" and not board and not q and page == 1:
        notices = (Post.query.filter_by(community_id=community.id, is_notice=True)
                   .order_by(Post.id.desc()).limit(5).all())
    return render_template(
        "communi.html", community=community, popular=community_popular(community),
        pagination=pagination, mode=mode, q=q, board=board, notices=notices,
    )


@bp.route("/search")
def search():
    q = request.args.get("q", "").strip()
    page = request.args.get("page", 1, type=int)
    pagination = None
    communities = []
    if len(q) >= 2:
        pagination = (
            Post.query.filter(Post.title.contains(q) | Post.text.contains(q))
            .order_by(Post.id.desc())
            .paginate(page=page, per_page=current_app.config["POSTS_PER_PAGE"], error_out=False)
        )
        communities = Community.query.filter(Community.name.contains(q)).limit(10).all()
    return render_template("search.html", q=q, pagination=pagination, communities=communities)


# ---------- 시리즈 ----------

@bp.route("/series", methods=["POST"])
def series_create():
    if g.user is None:
        return jsonify(error="로그인이 필요합니다."), 401
    title = (request.form.get("title") or "").strip()
    if not 1 <= len(title) <= 40:
        return jsonify(error="시리즈 이름은 1~40자로 정해주세요."), 400
    if Series.query.filter_by(user_id=g.user.id).count() >= 50:
        return jsonify(error="시리즈는 50개까지 만들 수 있어요."), 400
    s = Series(user_id=g.user.id, title=title)
    db.session.add(s)
    db.session.commit()
    return jsonify(id=s.id, title=s.title)


@bp.route("/series/<int:series_id>")
def series_view(series_id):
    s = db.session.get(Series, series_id) or abort(404)
    posts = Post.query.filter_by(series_id=s.id).order_by(Post.id).all()
    return render_template("series.html", series=s, posts=posts, owner=db.session.get(User, s.user_id))


# ---------- 글 ----------

def _save_post(community, post, form):
    """글쓰기/수정 공통 처리. 성공하면 Post, 실패하면 오류 메시지(str)."""
    title = form.get("title", "").strip()
    error = validate_title(title)
    if error:
        return error
    board, error = pick_board(community, form.get("board_id", type=int), post.board_id if post else None)
    if error:
        return error
    author_id = post.user_id if post else g.user.id
    author = post.author if post else g.user
    try:
        stored, text, has_image, has_video = content_mod.prepare(
            form.get("content", ""), author, current_app.config["POST_MAX_IMAGES"])
    except content_mod.ContentError as e:
        return str(e)
    series = pick_series(form.get("series_id", type=int), author_id)
    if post is None:
        poll, error = parse_poll(form)
        if error:
            return error
        wait = cooldown_left(g.user.last_post_at, current_app.config["POST_COOLDOWN"])
        if wait:
            return f"도배 방지: {wait}초 후에 다시 작성할 수 있습니다."
        reward = reward_amount(g.user, "post")
        post = Post(community_id=community.id, user_id=g.user.id, points_awarded=reward, has_poll=bool(poll))
        db.session.add(post)
        post.title, post.content, post.text, post.fmt = title, stored, text, "html"
        post.has_image, post.has_video, post.board_id = has_image, has_video, board.id if board else None
        post.series_id = series.id if series else None
        db.session.flush()
        if poll:
            ends = utcnow() + timedelta(days=poll["days"]) if poll["days"] else None
            p = Poll(post_id=post.id, question=poll["question"], multiple=poll["multiple"], ends_at=ends)
            p.options = [PollOption(text=t, sort_order=i) for i, t in enumerate(poll["options"])]
            db.session.add(p)
        g.user.last_post_at = utcnow()
        if reward:
            add_points(g.user, reward, "post", f"글 작성: {title}")
    else:
        post.title, post.content, post.text, post.fmt = title, stored, text, "html"
        post.has_image, post.has_video, post.board_id = has_image, has_video, board.id if board else None
        post.series_id = series.id if series else None
        post.updated_at = utcnow()
    db.session.commit()
    return post


@bp.route("/c/<slug>/write", methods=["GET", "POST"])
@login_required
def write(slug):
    community = get_community(slug)
    ctx = editor_context(community)
    if community.boards and not ctx["boards"]:
        flash("이 커뮤니티에는 글을 쓸 수 있는 게시판이 없어요.", "error")
        return redirect(url_for("board.communi", slug=slug))
    form_state = {"title": "", "html": "", "board_id": request.args.get("board", type=int), "series_id": None}
    if request.method == "POST":
        result = _save_post(community, None, request.form)
        if isinstance(result, Post):
            r = result.points_awarded
            flash(f"글이 등록되었습니다. +{r}P" if r else "글이 등록되었습니다. (오늘 글 포인트 한도 도달)", "success")
            return redirect(url_for("board.post", slug=slug, post_id=result.id))
        db.session.rollback()
        flash(result, "error")
        raw = request.form.get("content", "")
        form_state = {"title": request.form.get("title", ""), "board_id": request.form.get("board_id", type=int),
                      "series_id": request.form.get("series_id", type=int),
                      "html": content_mod.render(content_mod.clean_html(raw), g.user.id, editing=True)}
    return render_template("write.html", form=form_state, poll_form=request.form if request.method == "POST" else None,
                           **ctx)


@bp.route("/c/<slug>/<int:post_id>/edit", methods=["GET", "POST"])
@login_required
def edit(slug, post_id):
    community = get_community(slug)
    p = get_post(community, post_id)
    if not can_edit(p):
        abort(403)
    ctx = editor_context(community, p)
    if p.board and p.board not in ctx["boards"]:
        ctx["boards"] = ctx["boards"] + [p.board]  # 관리자 전용 게시판의 글이라도 원래 게시판은 유지 가능
    stored = p.content if p.fmt == "html" else content_mod.legacy_to_html(p.content)
    form_state = {"title": p.title, "board_id": p.board_id, "series_id": p.series_id,
                  "html": content_mod.render(stored, p.user_id, editing=True)}
    if request.method == "POST":
        result = _save_post(community, p, request.form)
        if isinstance(result, Post):
            flash("글이 수정되었습니다.", "success")
            return redirect(url_for("board.post", slug=slug, post_id=p.id))
        db.session.rollback()
        flash(result, "error")
        form_state.update(title=request.form.get("title", ""),
                          html=content_mod.render(content_mod.clean_html(request.form.get("content", "")),
                                                  p.user_id, editing=True))
    return render_template("write.html", form=form_state, poll_form=None, **ctx)


@bp.route("/c/<slug>/<int:post_id>")
def post(slug, post_id):
    community = get_community(slug)
    p = get_post(community, post_id)

    viewed = session.get("viewed", [])
    if p.id not in viewed:
        p.views = Post.views + 1
        db.session.commit()
        session["viewed"] = (viewed + [p.id])[-200:]

    my_vote, my_packs, my_poll = None, [], None
    if g.user:
        v = Vote.query.filter_by(post_id=p.id, user_id=g.user.id).first()
        my_vote = v.value if v else None
        my_packs = [up.pack for up in UserConPack.query.filter_by(user_id=g.user.id).order_by(UserConPack.id)]
        if p.poll:
            pv = PollVote.query.filter_by(poll_id=p.poll.id, user_id=g.user.id).first()
            my_poll = pv.option_set if pv else None
    if p.fmt == "html":
        body = content_mod.render(p.content, p.user_id)
    else:
        from models import Upload
        ids = image_token_ids(p.content)
        imgs = {u.id: u for u in Upload.query.filter(Upload.id.in_(ids), Upload.user_id == p.user_id)} if ids else {}
        body = render_body(p.content, imgs)
    series_posts = Post.query.filter_by(series_id=p.series_id).order_by(Post.id).limit(100).all() if p.series_id else []
    return render_template(
        "post.html", community=community, post=p, my_vote=my_vote, my_packs=my_packs, my_poll=my_poll,
        body=body, series_posts=series_posts, popular=community_popular(community),
        can_edit=can_edit(p), can_delete=can_delete(p, community), is_manager=community.is_manager(g.user),
    )


@bp.route("/c/<slug>/<int:post_id>/delete", methods=["POST"])
@login_required
def delete_post(slug, post_id):
    community = get_community(slug)
    p = get_post(community, post_id)
    if not can_delete(p, community):
        abort(403)
    # 글 작성 포인트만 회수 (댓글 단 사람들의 포인트는 회수하지 않음)
    revoked, mine = p.points_awarded, p.user_id == g.user.id
    if revoked:
        add_points(p.author, -revoked, "revoke", f"글 삭제: {p.title}")
    db.session.delete(p)
    db.session.commit()
    flash("글이 삭제되었습니다." + (f" (-{revoked}P)" if revoked and mine else ""), "info")
    return redirect(url_for("board.communi", slug=slug))


@bp.route("/c/<slug>/<int:post_id>/notice", methods=["POST"])
@login_required
def toggle_notice(slug, post_id):
    community = get_community(slug)
    p = get_post(community, post_id)
    if not community.is_manager(g.user):
        abort(403)
    p.is_notice = not p.is_notice
    db.session.commit()
    flash("공지로 등록했어요." if p.is_notice else "공지를 해제했어요.", "info")
    return redirect(url_for("board.post", slug=slug, post_id=p.id))


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


@bp.route("/c/<slug>/<int:post_id>/poll", methods=["POST"])
@login_required
def poll_vote(slug, post_id):
    community = get_community(slug)
    p = get_post(community, post_id)
    poll = p.poll or abort(404)
    back = url_for("board.post", slug=slug, post_id=p.id) + "#poll"
    valid = {o.id for o in poll.options}
    chosen = [i for i in dict.fromkeys(request.form.getlist("option", type=int)) if i in valid]
    if poll.closed:
        flash("마감된 투표예요.", "error")
    elif not chosen:
        flash("투표할 항목을 선택해주세요.", "error")
    elif not poll.multiple and len(chosen) > 1:
        flash("하나만 선택할 수 있어요.", "error")
    else:
        try:
            db.session.add(PollVote(poll_id=poll.id, user_id=g.user.id, option_ids=",".join(map(str, chosen))))
            db.session.flush()  # 이미 투표했으면 여기서 IntegrityError
            db.session.execute(update(PollOption).where(PollOption.id.in_(chosen))
                               .values(votes=PollOption.votes + 1))
            poll.voter_count = Poll.voter_count + 1
            db.session.commit()
            flash("투표했어요!", "success")
        except IntegrityError:
            db.session.rollback()
            flash("이미 투표했어요.", "error")
    return redirect(back)


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
    p = c.post
    if not can_delete(c, p.community):
        abort(403)
    slug, post_id = p.community.slug, p.id
    if c.points_awarded:
        add_points(c.author, -c.points_awarded, "revoke", f"댓글 삭제: {p.title}")
    p.comment_count = Post.comment_count - 1
    db.session.delete(c)
    db.session.commit()
    return redirect(url_for("board.post", slug=slug, post_id=post_id) + "#comments")
