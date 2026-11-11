"""알림

- 내 글에 댓글 / 내 댓글에 답글 (회원만, 내가 쓴 건 알림 안 감, 마이페이지에서 끌 수 있음)
- 가입한 커뮤니티의 공지 / 새 글 (커뮤니티마다 끄기·공지만·새 글 전부)
- 커뮤니티 개설 승인
"""
from datetime import timedelta

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from models import CommunityMember, Notification, User, db, utcnow
from ranks import public_name
from utils import login_required

bp = Blueprint("notify", __name__)

KEEP_PER_USER = 200
MAX_FANOUT = 2000  # 새 글 알림을 한 번에 보내는 최대 인원


def _cut(text, n):
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[:n - 1] + "…"


def actor_label(user=None, anon_name=None):
    return public_name(user) if user else (anon_name or "ㅇㅇ")


def send(user_id, kind, message, link):
    if not user_id or (g.user is not None and g.user.id == user_id):
        return  # 나 자신에게는 보내지 않음
    db.session.add(Notification(user_id=user_id, kind=kind, message=_cut(message, 300), link=link[:300]))


def on_comment(post, comment, reply_target=None):
    """새 댓글: 글쓴이에게 '댓글', 답글 대상 댓글 쓴이에게 '답글' 알림"""
    who = actor_label(g.user, comment.anon_name)
    link = url_for("board.post", slug=post.community.slug, post_id=post.id) + f"#c{comment.id}"
    body = _cut(comment.content or "(커뮤콘)", 60)
    sent = set()
    if reply_target is not None and reply_target.user_id and reply_target.author \
            and reply_target.author.notify_comments:
        send(reply_target.user_id, "reply", f"{who}님이 내 댓글에 답글을 달았어요: {body}", link)
        sent.add(reply_target.user_id)
    if post.user_id and post.user_id not in sent and post.author and post.author.notify_comments:
        send(post.user_id, "comment", f"{who}님이 내 글 '{_cut(post.title, 30)}'에 댓글을 달았어요: {body}", link)


def _members(community, levels, exclude):
    q = (db.session.query(CommunityMember.user_id)
         .filter(CommunityMember.community_id == community.id, CommunityMember.notify.in_(levels)))
    return [uid for (uid,) in q.limit(MAX_FANOUT) if uid not in exclude]


def on_new_post(post):
    """가입한 커뮤니티에 새 글 (알림을 '새 글 전부'로 한 회원)"""
    link = url_for("board.post", slug=post.community.slug, post_id=post.id)
    msg = f"[{post.community.name}] 새 글: {_cut(post.title, 50)}"
    db.session.add_all([Notification(user_id=uid, kind="new_post", message=msg, link=link)
                        for uid in _members(post.community, ("all",), {post.user_id, g.user.id if g.user else None})])


def on_notice(post):
    """공지로 올라간 글 (알림을 '공지만' 또는 '새 글 전부'로 한 회원)"""
    link = url_for("board.post", slug=post.community.slug, post_id=post.id)
    msg = f"[{post.community.name}] 공지: {_cut(post.title, 50)}"
    # 공지를 껐다 켜도 같은 글로 두 번 알리지 않음
    already = {uid for (uid,) in db.session.query(Notification.user_id).filter_by(kind="notice", link=link)}
    db.session.add_all([Notification(user_id=uid, kind="notice", message=msg, link=link)
                        for uid in _members(post.community, ("notice", "all"), {g.user.id if g.user else None} | already)])


def on_community_approved(community):
    if community.creator_id:
        db.session.add(Notification(user_id=community.creator_id, kind="approved",
                                    message=f"'{community.name}' 커뮤니티 개설이 승인됐어요! 이제 모두에게 공개돼요.",
                                    link=url_for("board.communi", slug=community.slug)))


def unread_count(user):
    return Notification.query.filter_by(user_id=user.id, is_read=False).count() if user else 0


# ---------- 화면 ----------

@bp.route("/notifications")
@login_required
def index():
    # 오래된 알림 정리 (최근 200개만, 90일 지난 읽은 알림은 삭제)
    old_ids = [n.id for n in Notification.query.filter_by(user_id=g.user.id)
               .order_by(Notification.id.desc()).offset(KEEP_PER_USER).limit(500)]
    if old_ids:
        Notification.query.filter(Notification.id.in_(old_ids)).delete(synchronize_session=False)
    Notification.query.filter(Notification.user_id == g.user.id, Notification.is_read.is_(True),
                              Notification.created_at < utcnow() - timedelta(days=90)).delete(synchronize_session=False)
    db.session.commit()
    page = request.args.get("page", 1, type=int)
    pagination = (Notification.query.filter_by(user_id=g.user.id).order_by(Notification.id.desc())
                  .paginate(page=page, per_page=30, error_out=False))
    return render_template("notifications.html", pagination=pagination)


@bp.route("/notifications/<int:nid>")
@login_required
def go(nid):
    n = db.session.get(Notification, nid)
    if not n or n.user_id != g.user.id:
        abort(404)
    n.is_read = True
    db.session.commit()
    link = n.link if n.link.startswith("/") and not n.link.startswith("//") else url_for("notify.index")
    return redirect(link)


@bp.route("/notifications/read-all", methods=["POST"])
@login_required
def read_all():
    Notification.query.filter_by(user_id=g.user.id, is_read=False).update({"is_read": True})
    db.session.commit()
    return redirect(url_for("notify.index"))


@bp.route("/notifications/clear", methods=["POST"])
@login_required
def clear():
    Notification.query.filter_by(user_id=g.user.id).delete()
    db.session.commit()
    flash("알림을 모두 지웠어요.", "info")
    return redirect(url_for("notify.index"))


@bp.route("/notifications/settings", methods=["POST"])
@login_required
def settings():
    g.user.notify_comments = request.form.get("notify_comments") == "1"
    db.session.commit()
    flash("내 글·댓글에 달린 댓글 알림을 켰어요." if g.user.notify_comments else "내 글·댓글에 달린 댓글 알림을 껐어요.", "info")
    return redirect(url_for("notify.index"))
