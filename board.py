"""커뮤니티 / 게시판 / 게시글 / 댓글 / 추천 / 투표 / 시리즈 / 검색 / 커뮤니티 관리 (부관리자 승인 포함)"""
import json
import re
import time
from datetime import timedelta

from flask import (Blueprint, abort, current_app, flash, g, jsonify, redirect, render_template, request, session,
                   url_for)
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash

import captcha
import content as content_mod
import notify
from models import (Board, Comment, Community, CommunityMember, CommunityStaff, ConItem, ModRequest, Poll, PollOption, PollVote, Post,
                    Series, User, UserConPack, Vote, db, utcnow)
from uploads import owned_upload
from utils import (add_points, community_popular, cooldown_left, image_token_ids, login_required, popular_filter,
                   render_body, reward_amount)

bp = Blueprint("board", __name__)
SLUG_RE = re.compile(r"^[a-z0-9_]{2,20}$")
RESERVED_SLUGS = {"new", "admin", "api", "static"}
RESERVED_NAMES = ("관리자", "운영자", "탈퇴회원", "부관리자")
POLL_DAYS = {0: "마감 없음", 1: "1일", 3: "3일", 7: "7일"}
MAX_BOARDS = 20
MAX_STAFF = 10
ANON_OK_SECONDS = 600  # 익명 비밀번호 확인 후 수정 가능한 시간


def get_community(slug, need_open=False):
    """승인 전 커뮤니티는 만든 사람/사이트 관리자에게만 보인다.
    need_open=True: 글쓰기·댓글·추천처럼 승인된 커뮤니티에서만 되는 작업"""
    c = Community.query.filter_by(slug=slug).first_or_404()
    if not c.visible_to(g.user):
        abort(404)
    if need_open and not c.approved:
        abort(403, "사이트 관리자의 승인을 기다리는 커뮤니티예요. 승인 후 글을 쓸 수 있어요.")
    return c


def get_post(community, post_id):
    post = db.session.get(Post, post_id)
    if not post or post.community_id != community.id:
        abort(404)
    return post


def client_ip():
    return request.remote_addr or ""


# ---------- 권한 ----------

def _anon_key(obj):
    return f"{'p' if isinstance(obj, Post) else 'c'}{obj.id}"


def anon_ok(obj):
    """익명 글/댓글: 비밀번호를 확인한 지 10분 안이면 True"""
    return obj.user_id is None and session.get("anon_ok", {}).get(_anon_key(obj), 0) > time.time()


def grant_anon(obj):
    ok = {k: v for k, v in session.get("anon_ok", {}).items() if v > time.time()}
    ok[_anon_key(obj)] = time.time() + ANON_OK_SECONDS
    session["anon_ok"] = ok


def can_edit(obj):
    """수정: 쓴 사람, 사이트 관리자, (익명이면) 비밀번호 확인한 사람"""
    if obj.user_id is None:
        return anon_ok(obj) or bool(g.user and g.user.is_admin)
    return g.user is not None and (obj.user_id == g.user.id or g.user.is_admin)


def can_delete(obj, community):
    """바로 삭제: 쓴 사람, 사이트 관리자, 커뮤니티 관리자 (부관리자는 요청만)"""
    return can_edit(obj) or community.is_manager(g.user)


# ---------- 입력 검사 ----------

def validate_title(title):
    return None if 1 <= len(title) <= 100 else "제목은 1~100자로 입력해주세요."


def validate_anon(form):
    """익명 작성자 정보 → (이름, 비밀번호 해시, 오류)"""
    name = (form.get("anon_name") or "").strip() or "ㅇㅇ"
    pw = form.get("anon_password") or ""
    if not 1 <= len(name) <= 12:
        return None, None, "닉네임은 12자 이하로 입력해주세요."
    if name.startswith(RESERVED_NAMES) or             User.query.filter(User.is_admin.is_(True), db.func.lower(User.nickname) == name.lower()).first():
        return None, None, "사용할 수 없는 닉네임입니다."
    if not 4 <= len(pw) <= 30:
        return None, None, "비밀번호를 4~30자로 입력해주세요. (수정·삭제할 때 필요해요)"
    if not captcha.check(form.get("captcha")):
        return None, None, "보안 코드가 올바르지 않아요."
    return name, generate_password_hash(pw), None


def anon_cooldown(model, seconds):
    last = model.query.filter(model.anon_ip == client_ip()).order_by(model.id.desc()).first()
    return cooldown_left(last.created_at if last else None, seconds)


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
    if b.manager_only and not community.is_staff(g.user) and b.id != keep_id:
        return None, f"'{b.name}' 게시판은 관리자만 글을 쓸 수 있어요."
    return b, None


def pick_series(series_id, user_id):
    if not series_id or not user_id:
        return None
    s = db.session.get(Series, series_id)
    return s if s and s.user_id == user_id else None


def editor_context(community, post=None):
    my_packs, my_series = [], []
    if g.user:
        my_packs = [up.pack for up in UserConPack.query.filter_by(user_id=g.user.id).order_by(UserConPack.id)]
        owner = post.user_id if post else g.user.id
        if owner:
            my_series = Series.query.filter_by(user_id=owner).order_by(Series.id.desc()).all()
    return {
        "community": community, "post": post, "poll_days": POLL_DAYS, "my_packs": my_packs, "my_series": my_series,
        "boards": community.writable_boards(g.user),
        "video_max_mb": current_app.config["VIDEO_MAX_BYTES"] // (1024 * 1024),
        "member_tools": bool(g.user) and (post is None or post.user_id is not None),
    }


# ---------- 관리 작업 (관리자 바로 실행 / 부관리자 요청 승인 후 실행) ----------

def do_delete_post(p):
    if p.author:
        if p.points_awarded:
            add_points(p.author, -p.points_awarded, "revoke", f"글 삭제: {p.title}")
        p.author.post_count = User.post_count - 1
    db.session.delete(p)


def do_delete_comment(c):
    p = c.post
    if c.author and c.points_awarded:
        add_points(c.author, -c.points_awarded, "revoke", f"댓글 삭제: {p.title}")
    p.comment_count = Post.comment_count - 1
    Comment.query.filter_by(parent_id=c.id).update({"parent_id": None}, synchronize_session=False)
    db.session.delete(c)


def settings_from_form(form, user):
    """관리 화면 입력 → 설정 변경 내용 (dict)"""
    rows = list(zip(form.getlist("board_id"), [n.strip() for n in form.getlist("board_name")], form.getlist("board_perm")))
    data = {"description": form.get("description", "").strip(),
            "boards": [[i, n, p] for i, n, p in rows if n or i]}
    for field in ("icon", "banner"):
        if form.get(f"remove_{field}"):
            data[field] = ""
        up = owned_upload(form.get(f"{field}_id", type=int), user, field)
        if up:
            data[field] = up.path
    return data


def validate_settings(data):
    if len(data["description"]) > 200:
        return "소개는 200자 이하로 입력해주세요."
    rows = data["boards"]
    if len(rows) > MAX_BOARDS:
        return f"게시판은 {MAX_BOARDS}개까지 만들 수 있어요."
    if any(not 1 <= len(n) <= 20 for _, n, _ in rows):
        return "게시판 이름은 1~20자로 정해주세요."
    if len({n for _, n, _ in rows}) != len(rows):
        return "같은 이름의 게시판이 있어요."
    return None


def apply_settings(community, data):
    community.description = data["description"]
    existing = {str(b.id): b for b in community.boards}
    keep = set()
    for order, (bid, name, perm) in enumerate(data["boards"]):
        b = existing.get(str(bid))
        if not b:
            b = Board(community=community)
            db.session.add(b)
        b.name, b.sort_order, b.manager_only = name, order, perm == "manager"
        if b.id:
            keep.add(str(b.id))
    for bid, b in existing.items():
        if bid not in keep:
            Post.query.filter_by(board_id=b.id).update({"board_id": None})
            db.session.delete(b)
    for field in ("icon", "banner"):
        if field in data:
            setattr(community, field, data[field] or None)


def make_request(community, action, target_id=None, summary="", payload=None):
    """부관리자 요청. 같은 대상에 대기 중인 같은 요청이 있으면 만들지 않는다."""
    dup = community.requests.filter_by(action=action, target_id=target_id, status="pending",
                                       requester_id=g.user.id).first()
    if dup and action != "settings":
        return False
    if action == "settings" and dup:
        dup.payload, dup.summary, dup.created_at = json.dumps(payload, ensure_ascii=False), summary, utcnow()
    else:
        db.session.add(ModRequest(community_id=community.id, requester_id=g.user.id, action=action,
                                  target_id=target_id, summary=summary[:200],
                                  payload=json.dumps(payload, ensure_ascii=False) if payload else None))
    db.session.commit()
    return True


def execute_request(community, req):
    """승인된 요청 실행. 대상이 이미 사라졌으면 그냥 넘어간다."""
    if req.action == "settings":
        data = json.loads(req.payload or "{}")
        if validate_settings(data) is None:
            apply_settings(community, data)
    elif req.action in ("delete_post", "notice"):
        p = db.session.get(Post, req.target_id)
        if p and p.community_id == community.id:
            if req.action == "delete_post":
                do_delete_post(p)
            else:
                p.is_notice = not p.is_notice
                if p.is_notice:
                    notify.on_notice(p)
    elif req.action == "delete_comment":
        c = db.session.get(Comment, req.target_id)
        if c and c.post.community_id == community.id:
            do_delete_comment(c)


# ---------- 커뮤니티 ----------

@bp.route("/c")
def communi_list():
    q = request.args.get("q", "").strip()
    members = (db.select(db.func.count(CommunityMember.id))
               .where(CommunityMember.community_id == Community.id).scalar_subquery())
    rows = (
        db.session.query(Community, db.func.count(Post.id), members)
        .outerjoin(Post, Post.community_id == Community.id)
        .filter(Community.status == "approved")
        .group_by(Community.id)
    )
    if q:
        rows = rows.filter(Community.name.contains(q) | Community.description.contains(q))
    rows = rows.order_by(db.func.count(Post.id).desc(), Community.name).all()
    return render_template("communi_list.html", rows=rows, q=q)


@bp.route("/c/new", methods=["GET", "POST"])
@login_required
def communi_new():
    # 커뮤니티는 한 사람당 하나만 만들 수 있다 (사이트 관리자는 운영용으로 예외)
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
            c = Community(creator_id=g.user.id, status="approved" if g.user.is_admin else "pending", **form)
            db.session.add(c)
            db.session.flush()
            db.session.add(CommunityMember(community_id=c.id, user_id=g.user.id))  # 만든 사람은 자동 가입
            try:
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                error = "이미 존재하는 커뮤니티 이름 또는 주소입니다."
            else:
                if c.approved:
                    flash(f"'{c.name}' 커뮤니티가 만들어졌습니다! 관리 메뉴에서 게시판, 아이콘, 배너를 꾸며보세요.", "success")
                else:
                    flash(f"'{c.name}' 커뮤니티 개설을 신청했어요. 사이트 관리자가 승인하면 열려요. "
                          "그동안 관리 메뉴에서 게시판, 아이콘, 배너를 미리 꾸밀 수 있어요.", "info")
                return redirect(url_for("board.communi", slug=c.slug))
        flash(error, "error")
    return render_template("communi_new.html", form=form)


@bp.route("/c/<slug>/notify", methods=["POST"])
@login_required
def notify_level(slug):
    """가입한 커뮤니티 알림: off / notice(공지만) / all(새 글 전부)"""
    community = get_community(slug, need_open=True)
    level = request.form.get("level")
    m = community.members.filter_by(user_id=g.user.id).first()
    if not m:
        flash("먼저 커뮤니티에 가입해 주세요.", "error")
    elif level in ("off", "notice", "all"):
        m.notify = level
        db.session.commit()
        flash({"off": "이 커뮤니티 알림을 껐어요.", "notice": "이제 공지가 올라오면 알려드려요.",
               "all": "이제 새 글이 올라올 때마다 알려드려요."}[level], "info")
    return redirect(url_for("board.communi", slug=slug))


@bp.route("/c/<slug>/join", methods=["POST"])
@login_required
def join(slug):
    community = get_community(slug, need_open=True)
    back = request.form.get("next") or url_for("board.communi", slug=slug)
    if not back.startswith("/") or back.startswith("//"):
        back = url_for("board.communi", slug=slug)
    if request.form.get("leave"):
        if g.user.id == community.creator_id:
            flash("커뮤니티 관리자는 자기 커뮤니티에서 탈퇴할 수 없어요.", "error")
        else:
            community.members.filter_by(user_id=g.user.id).delete()
            CommunityStaff.query.filter_by(community_id=community.id, user_id=g.user.id).delete()  # 탈퇴하면 부관리자도 해제
            db.session.commit()
            flash(f"'{community.name}' 커뮤니티에서 탈퇴했어요.", "info")
        return redirect(back)
    try:
        db.session.add(CommunityMember(community_id=community.id, user_id=g.user.id))
        db.session.commit()
        flash(f"'{community.name}' 커뮤니티에 가입했어요!", "success")
    except IntegrityError:
        db.session.rollback()
    return redirect(back)


@bp.route("/c/<slug>/manage", methods=["GET", "POST"])
@login_required
def manage(slug):
    community = get_community(slug)
    if not community.is_staff(g.user):
        abort(403)
    is_manager = community.is_manager(g.user)
    if request.method == "POST":
        data = settings_from_form(request.form, g.user)
        error = validate_settings(data)
        if error:
            flash(error, "error")
        elif is_manager:
            apply_settings(community, data)
            db.session.commit()
            flash("커뮤니티 설정이 저장되었습니다.", "success")
            return redirect(url_for("board.communi", slug=slug))
        else:
            make_request(community, "settings", summary="커뮤니티 설정 변경", payload=data)
            flash("설정 변경을 요청했어요. 커뮤니티 관리자가 승인하면 반영돼요.", "info")
            return redirect(url_for("board.manage", slug=slug))
    pending = community.requests.filter_by(status="pending").order_by(ModRequest.id.desc()).all() if is_manager else []
    mine = community.requests.filter_by(requester_id=g.user.id).order_by(ModRequest.id.desc()).limit(10).all() \
        if not is_manager else []
    return render_template("communi_manage.html", community=community, is_manager=is_manager,
                           pending=pending, my_requests=mine)


@bp.route("/c/<slug>/staff", methods=["POST"])
@login_required
def staff(slug):
    community = get_community(slug)
    if not community.is_manager(g.user):
        abort(403)
    back = url_for("board.manage", slug=slug) + "#staff"
    remove_id = request.form.get("remove", type=int)
    if remove_id:
        CommunityStaff.query.filter_by(community_id=community.id, user_id=remove_id).delete()
        db.session.commit()
        flash("부관리자를 해제했어요.", "info")
        return redirect(back)
    nickname = (request.form.get("nickname") or "").strip()
    user = User.query.filter(db.func.lower(User.nickname) == nickname.lower()).first() if nickname else None
    if not user or user.username is None:
        flash("그 닉네임의 회원이 없어요.", "error")
    elif user.id == community.creator_id or user.is_admin:
        flash("이 회원은 부관리자로 지정할 수 없어요.", "error")
    elif user.id in community.sub_ids:
        flash("이미 부관리자예요.", "info")
    elif len(community.staff) >= MAX_STAFF:
        flash(f"부관리자는 {MAX_STAFF}명까지 지정할 수 있어요.", "error")
    else:
        db.session.add(CommunityStaff(community_id=community.id, user_id=user.id))
        db.session.commit()
        flash(f"{user.nickname} 님을 부관리자로 지정했어요.", "success")
    return redirect(back)


@bp.route("/c/<slug>/requests/<int:req_id>", methods=["POST"])
@login_required
def decide_request(slug, req_id):
    community = get_community(slug)
    if not community.is_manager(g.user):
        abort(403)
    req = db.session.get(ModRequest, req_id)
    if not req or req.community_id != community.id or req.status != "pending":
        abort(404)
    approve = request.form.get("decision") == "approve"
    if approve:
        execute_request(community, req)
    req.status = "approved" if approve else "rejected"
    req.decided_at, req.decided_by = utcnow(), g.user.id
    db.session.commit()
    flash("요청을 승인했어요." if approve else "요청을 거절했어요.", "success" if approve else "info")
    return redirect(url_for("board.manage", slug=slug) + "#requests")


@bp.route("/c/<slug>/delete", methods=["POST"])
@login_required
def delete_community(slug):
    community = get_community(slug)
    if not community.is_manager(g.user):
        abort(403)
    if (request.form.get("confirm_name") or "").strip() != community.name:
        flash("확인을 위해 커뮤니티 이름을 정확히 입력해주세요.", "error")
        return redirect(url_for("board.manage", slug=slug) + "#danger")
    name = community.name
    # 글쓴이들의 글 수(장로 판정용)를 맞춰준다
    for uid, n in (db.session.query(Post.user_id, db.func.count(Post.id))
                   .filter(Post.community_id == community.id, Post.user_id.isnot(None)).group_by(Post.user_id)):
        db.session.execute(update(User).where(User.id == uid).values(post_count=User.post_count - n))
    db.session.delete(community)
    db.session.commit()
    flash(f"'{name}' 커뮤니티를 삭제했어요.", "info")
    return redirect(url_for("main.home"))


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
        communities = Community.query.filter(Community.status == "approved", Community.name.contains(q)).limit(10).all()
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
    author = post.author if post else g.user  # None 이면 익명
    try:
        stored, text, has_image, has_video = content_mod.prepare(
            form.get("content", ""), author, current_app.config["POST_MAX_IMAGES"])
    except content_mod.ContentError as e:
        return str(e)
    series = pick_series(form.get("series_id", type=int), author.id if author else None)
    if post is None:
        anon_name = anon_pw = None
        if g.user is None:
            anon_name, anon_pw, error = validate_anon(form)
            if error:
                return error
            wait = anon_cooldown(Post, current_app.config["POST_COOLDOWN"])
            poll = None
        else:
            poll, error = parse_poll(form)
            if error:
                return error
            wait = cooldown_left(g.user.last_post_at, current_app.config["POST_COOLDOWN"])
        if wait:
            return f"도배 방지: {wait}초 후에 다시 작성할 수 있습니다."
        reward = reward_amount(g.user, "post") if g.user else 0
        post = Post(community_id=community.id, user_id=g.user.id if g.user else None, points_awarded=reward,
                    has_poll=bool(poll), anon_name=anon_name, anon_pw_hash=anon_pw,
                    anon_ip=client_ip() if g.user is None else None)
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
        if g.user:
            g.user.last_post_at = utcnow()
            g.user.post_count = User.post_count + 1
            if reward:
                add_points(g.user, reward, "post", f"글 작성: {title}")
        notify.on_new_post(post)
    else:
        post.title, post.content, post.text, post.fmt = title, stored, text, "html"
        post.has_image, post.has_video, post.board_id = has_image, has_video, board.id if board else None
        post.series_id = series.id if series else None
        post.updated_at = utcnow()
    db.session.commit()
    return post


@bp.route("/c/<slug>/write", methods=["GET", "POST"])
def write(slug):
    community = get_community(slug, need_open=True)
    ctx = editor_context(community)
    if community.boards and not ctx["boards"]:
        flash("이 커뮤니티에는 글을 쓸 수 있는 게시판이 없어요.", "error")
        return redirect(url_for("board.communi", slug=slug))
    form_state = {"title": "", "html": "", "board_id": request.args.get("board", type=int), "series_id": None,
                  "anon_name": ""}
    if request.method == "POST":
        result = _save_post(community, None, request.form)
        if isinstance(result, Post):
            r = result.points_awarded
            if g.user is None:
                flash("글이 등록되었습니다. (익명 글은 비밀번호로 수정·삭제할 수 있어요)", "success")
            else:
                flash(f"글이 등록되었습니다. +{r}P" if r else "글이 등록되었습니다. (오늘 글 포인트 한도 도달)", "success")
            return redirect(url_for("board.post", slug=slug, post_id=result.id))
        db.session.rollback()
        flash(result, "error")
        raw = request.form.get("content", "")
        form_state = {"title": request.form.get("title", ""), "board_id": request.form.get("board_id", type=int),
                      "series_id": request.form.get("series_id", type=int),
                      "anon_name": request.form.get("anon_name", ""),
                      "html": content_mod.render(content_mod.clean_html(raw), g.user.id if g.user else None,
                                                 editing=True)}
    return render_template("write.html", form=form_state, poll_form=request.form if request.method == "POST" else None,
                           **ctx)


@bp.route("/c/<slug>/<int:post_id>/check", methods=["POST"])
def anon_check(slug, post_id):
    """익명 글 비밀번호 확인 → 수정 화면으로 이동하거나 바로 삭제"""
    community = get_community(slug)
    p = get_post(community, post_id)
    if p.user_id is not None or not p.anon_pw_hash or \
            not check_password_hash(p.anon_pw_hash, request.form.get("anon_password", "")):
        flash("비밀번호가 올바르지 않습니다.", "error")
        return redirect(url_for("board.post", slug=slug, post_id=p.id))
    if request.form.get("then") == "delete":
        do_delete_post(p)
        db.session.commit()
        flash("글이 삭제되었습니다.", "info")
        return redirect(url_for("board.communi", slug=slug))
    grant_anon(p)
    return redirect(url_for("board.edit", slug=slug, post_id=p.id))


@bp.route("/c/<slug>/<int:post_id>/edit", methods=["GET", "POST"])
def edit(slug, post_id):
    community = get_community(slug)
    p = get_post(community, post_id)
    if not can_edit(p):
        if g.user is None and p.user_id is not None:
            return redirect(url_for("auth.login", next=request.path))
        abort(403)
    ctx = editor_context(community, p)
    if p.board and p.board not in ctx["boards"]:
        ctx["boards"] = ctx["boards"] + [p.board]  # 관리자 전용 게시판의 글이라도 원래 게시판은 유지 가능
    stored = p.content if p.fmt == "html" else content_mod.legacy_to_html(p.content)
    form_state = {"title": p.title, "board_id": p.board_id, "series_id": p.series_id, "anon_name": p.anon_name or "",
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
        can_edit=can_edit(p), can_delete=can_delete(p, community),
        is_manager=community.is_manager(g.user), is_sub=community.is_sub(g.user) and not community.is_manager(g.user),
    )


@bp.route("/c/<slug>/<int:post_id>/delete", methods=["POST"])
@login_required
def delete_post(slug, post_id):
    community = get_community(slug)
    p = get_post(community, post_id)
    if not can_delete(p, community):
        abort(403)
    revoked, mine = p.points_awarded, p.user_id == g.user.id
    do_delete_post(p)
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
    if p.is_notice:
        notify.on_notice(p)
    db.session.commit()
    flash("공지로 등록했어요. 가입한 회원들에게 알림이 가요." if p.is_notice else "공지를 해제했어요.", "info")
    return redirect(url_for("board.post", slug=slug, post_id=p.id))


@bp.route("/c/<slug>/<int:post_id>/request", methods=["POST"])
@login_required
def request_action(slug, post_id):
    """부관리자: 글 삭제 / 공지 등록·해제 요청"""
    community = get_community(slug, need_open=True)
    p = get_post(community, post_id)
    if not community.is_sub(g.user):
        abort(403)
    action = request.form.get("action")
    if action not in ("delete_post", "notice"):
        abort(400)
    label = "글 삭제" if action == "delete_post" else ("공지 해제" if p.is_notice else "공지 등록")
    if make_request(community, action, p.id, f"{label}: {p.title}"):
        flash(f"{label}을(를) 요청했어요. 커뮤니티 관리자가 승인하면 반영돼요.", "info")
    else:
        flash("이미 같은 요청을 보냈어요.", "info")
    return redirect(url_for("board.post", slug=slug, post_id=p.id))


@bp.route("/c/<slug>/<int:post_id>/vote", methods=["POST"])
@login_required
def vote(slug, post_id):
    community = get_community(slug, need_open=True)
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
    community = get_community(slug, need_open=True)
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
def comment(slug, post_id):
    community = get_community(slug, need_open=True)
    p = get_post(community, post_id)
    content = request.form.get("content", "").strip()
    con_id = request.form.get("con_id", type=int)
    back = url_for("board.post", slug=slug, post_id=p.id) + "#comments"
    reply_target = None
    reply_to = request.form.get("reply_to", type=int)
    if reply_to:
        reply_target = db.session.get(Comment, reply_to)
        if not reply_target or reply_target.post_id != p.id:
            reply_target = None

    con = None
    if con_id and g.user:
        con = db.session.get(ConItem, con_id)
        if not con or not g.user.owns_pack(con.pack_id):
            flash("보유하지 않은 커뮤콘입니다.", "error")
            return redirect(back)
    anon_name = anon_pw = None
    if g.user is None:
        anon_name, anon_pw, error = validate_anon(request.form)
        if error:
            flash(error, "error")
            return redirect(back)
        wait = anon_cooldown(Comment, current_app.config["COMMENT_COOLDOWN"])
    else:
        wait = cooldown_left(g.user.last_comment_at, current_app.config["COMMENT_COOLDOWN"])
    if not content and not con:
        flash("댓글 내용을 입력하거나 커뮤콘을 선택해주세요.", "error")
    elif len(content) > 1000:
        flash("댓글은 1,000자 이하로 입력해주세요.", "error")
    elif wait:
        flash(f"도배 방지: {wait}초 후에 다시 작성할 수 있습니다.", "error")
    else:
        reward = reward_amount(g.user, "comment") if g.user else 0
        c = Comment(post_id=p.id, user_id=g.user.id if g.user else None, content=content,
                    con_item_id=con.id if con else None, points_awarded=reward,
                    anon_name=anon_name, anon_pw_hash=anon_pw, anon_ip=client_ip() if g.user is None else None,
                    parent_id=(reply_target.parent_id or reply_target.id) if reply_target else None)
        db.session.add(c)
        db.session.flush()
        notify.on_comment(p, c, reply_target)
        p.comment_count = Post.comment_count + 1
        if g.user:
            g.user.last_comment_at = utcnow()
            if reward:
                add_points(g.user, reward, "comment", f"댓글 작성: {p.title}")
        db.session.commit()
        if reward:
            flash(f"댓글이 등록되었습니다. +{reward}P", "success")
        return redirect(url_for("board.post", slug=slug, post_id=p.id) + f"#c{c.id}")
    return redirect(back)


@bp.route("/comment/<int:comment_id>/delete", methods=["POST"])
def delete_comment(comment_id):
    c = db.session.get(Comment, comment_id) or abort(404)
    p = c.post
    community = p.community
    slug, post_id = community.slug, p.id
    back = url_for("board.post", slug=slug, post_id=post_id) + "#comments"
    allowed = g.user is not None and can_delete(c, community)
    if not allowed and c.user_id is None:  # 익명 댓글: 비밀번호로 삭제
        allowed = bool(c.anon_pw_hash) and check_password_hash(c.anon_pw_hash, request.form.get("anon_password", ""))
        if not allowed:
            flash("비밀번호가 올바르지 않습니다.", "error")
            return redirect(back)
    if not allowed:
        if g.user is None:
            return redirect(url_for("auth.login", next=request.referrer or "/"))
        abort(403)
    do_delete_comment(c)
    db.session.commit()
    return redirect(back)


@bp.route("/comment/<int:comment_id>/request", methods=["POST"])
@login_required
def request_comment_delete(comment_id):
    c = db.session.get(Comment, comment_id) or abort(404)
    community = c.post.community
    if not community.is_sub(g.user):
        abort(403)
    if make_request(community, "delete_comment", c.id, f"댓글 삭제: {(c.content or '커뮤콘')[:40]}"):
        flash("댓글 삭제를 요청했어요. 커뮤니티 관리자가 승인하면 반영돼요.", "info")
    else:
        flash("이미 같은 요청을 보냈어요.", "info")
    return redirect(url_for("board.post", slug=community.slug, post_id=c.post_id) + "#comments")
