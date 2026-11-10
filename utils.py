import hmac
import re
import secrets
from datetime import timedelta
from functools import wraps

from flask import abort, current_app, flash, g, redirect, request, session, url_for
from markupsafe import Markup, escape
from sqlalchemy import func, update

from models import PointLog, Post, User, Vote, db, utcnow

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
    """오픈 리다이렉트 방지: 우리 사이트 내부 경로만 허용"""
    if target and target.startswith("/") and not target.startswith(("//", "/\\")) and "\\" not in target:
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
    # SQL 식으로 더해서 동시에 여러 요청이 와도 포인트가 꼬이지 않게 한다 (points = points + amount)
    user.points = User.points + amount
    db.session.add(PointLog(user_id=user.id, amount=amount, kind=kind, reason=reason[:200]))
    db.session.flush()  # 같은 요청에서 다시 호출돼도 식이 덮어써지지 않게 바로 반영


def spend_points(user, amount, kind, reason=""):
    """포인트가 충분할 때만 차감. 성공하면 True."""
    result = db.session.execute(
        update(User).where(User.id == user.id, User.points >= amount).values(points=User.points - amount)
    )
    if result.rowcount != 1:
        return False
    db.session.add(PointLog(user_id=user.id, amount=-amount, kind=kind, reason=reason[:200]))
    return True


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
    """커뮤니티 상단 인기글: 최근 7일 내 추천 기준 이상 글"""
    since = utcnow() - timedelta(days=7)
    q = popular_filter(Post.query.filter(Post.community_id == community.id, Post.created_at >= since))
    return q.order_by(Post.upvotes.desc(), Post.id.desc()).limit(limit).all()


# ---------- 템플릿 필터 ----------

def kst(dt):
    return dt + KST if dt else dt


def short_time(dt):
    """목록용: 방금 전 / n분 전 / n시간 전 / 올해면 MM.DD / 그 이전은 YY.MM.DD"""
    if not dt:
        return ""
    seconds = (utcnow() - dt).total_seconds()
    if seconds < 60:
        return "방금 전"
    if seconds < 3600:
        return f"{int(seconds // 60)}분 전"
    if seconds < 86400:
        return f"{int(seconds // 3600)}시간 전"
    local = kst(dt)
    return local.strftime("%m.%d") if local.year == kst(utcnow()).year else local.strftime("%y.%m.%d")


def full_time(dt):
    return kst(dt).strftime("%Y.%m.%d %H:%M") if dt else ""


URL_RE = re.compile(r"https?://[^\s<>\"']+")


def _link(m):
    url = m.group(0).rstrip(".,)!?")
    rest = m.group(0)[len(url):]
    return f'<a href="{url}" target="_blank" rel="noopener nofollow ugc">{url}</a>{rest}'


def linkify(text):
    """escape 한 뒤 http(s) 주소만 링크로 바꾼다. HTML 주입 불가."""
    return URL_RE.sub(_link, str(escape(text or "")))


def nl2br(text):
    """줄바꿈을 <br>로, http(s) 주소는 링크로."""
    return Markup(linkify(text).replace("\n", "<br>"))


IMG_TOKEN_RE = re.compile(r"^\[\[img:(\d+)\]\]$")
YOUTUBE_RE = re.compile(
    r"^https?://(?:www\.|m\.)?(?:youtube\.com/(?:watch\?(?:[^\s#]*&)?v=|shorts/|embed/)|youtu\.be/)"
    r"([A-Za-z0-9_-]{11})(?:[?&#]\S*)?$"
)


def image_token_ids(content):
    return [int(m.group(1)) for line in (content or "").split("\n") if (m := IMG_TOKEN_RE.match(line.strip()))]


def render_body(content, images):
    """글 본문 → HTML.
    - [[img:번호]] 만 있는 줄: 사진 (images 에 있는 = 글쓴이가 올린 사진만)
    - 유튜브 주소만 있는 줄: 동영상
    - 나머지: 글자 (escape + 링크)"""
    html = []
    for raw in (content or "").split("\n"):
        line = raw.strip()
        m = IMG_TOKEN_RE.match(line)
        if m:
            up = images.get(int(m.group(1)))
            if up:
                size = f' width="{up.width}" height="{up.height}"' if up.width else ""
                html.append(f'<a class="post-img" href="{escape(con_url(up.path))}" target="_blank" rel="noopener">'
                            f'<img src="{escape(con_url(up.path))}"{size} loading="lazy" alt=""></a>')
            continue
        y = YOUTUBE_RE.match(line)
        if y:
            html.append(f'<div class="yt"><iframe src="https://www.youtube-nocookie.com/embed/{y.group(1)}" '
                        'title="YouTube" loading="lazy" allowfullscreen '
                        'allow="accelerometer; encrypted-media; gyroscope; picture-in-picture"></iframe></div>')
            continue
        html.append(linkify(raw) + "<br>")
    out = "".join(html)
    return Markup(out[:-4] if out.endswith("<br>") else out)


def con_url(path):
    """커뮤콘 이미지 주소: Blob 에 올린 건 전체 URL, 로컬/시드 이미지는 static 경로"""
    if path.startswith(("http://", "https://")):
        return path
    return url_for("static", filename=path)


def register_template_helpers(app):
    app.jinja_env.filters["con_url"] = con_url
    app.jinja_env.filters["linkify"] = lambda t: Markup(linkify(t))
    app.jinja_env.filters["short_time"] = short_time
    app.jinja_env.filters["full_time"] = full_time
    app.jinja_env.filters["nl2br"] = nl2br
    app.jinja_env.filters["comma"] = lambda n: f"{n or 0:,}"
    app.jinja_env.globals["csrf_token"] = csrf_token
