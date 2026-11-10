"""닉네임 색 (계급)

사이트 관리자  금색 + '관리자' 칭호
커뮤니티 관리자 은색
부관리자       동색
장로 회원      보라색 (글 500개 이상)
일반 회원      검은색
익명           회색 (+ IP 앞 두 자리)
계급은 커뮤니티마다 다르다 (A 커뮤니티 관리자가 B 커뮤니티에선 일반 회원).
"""
from flask import current_app
from markupsafe import Markup, escape

RANKS = {
    "admin": ("사이트 관리자", "rank-admin"),
    "manager": ("커뮤니티 관리자", "rank-manager"),
    "sub": ("부관리자", "rank-sub"),
    "elder": ("장로 회원", "rank-elder"),
    "member": ("회원", "rank-member"),
    "anon": ("익명", "rank-anon"),
}


def rank_of(user, community=None):
    if user is None:
        return "anon"
    if user.is_admin:
        return "admin"
    if community is not None:
        if user.id == community.creator_id:
            return "manager"
        if user.id in community.sub_ids:
            return "sub"
    if (user.post_count or 0) >= current_app.config["ELDER_POSTS"]:
        return "elder"
    return "member"


def mask_ip(ip):
    """IP 앞 두 자리만 (디시처럼): 123.45.67.89 → 123.45"""
    if not ip:
        return ""
    if ":" in ip:  # IPv6
        return ":".join(ip.split(":")[:2])
    return ".".join(ip.split(".")[:2])


def public_name(user):
    """다른 사람에게 보여줄 이름. 사이트 관리자는 실제 닉네임을 숨기고 '관리자'로만 보인다."""
    if user is None:
        return "ㅇㅇ"
    return "관리자" if user.is_admin else user.nickname


def name_tag(obj, community=None):
    """글/댓글(또는 회원)의 이름표 HTML"""
    user = obj if hasattr(obj, "nickname") else obj.author  # 회원 객체를 직접 넘겨도 되고, 글/댓글을 넘겨도 됨
    if user is None:
        name = getattr(obj, "anon_name", None) or "ㅇㅇ"
        ip = mask_ip(getattr(obj, "anon_ip", ""))
        tail = f'<span class="anon-ip">({escape(ip)})</span>' if ip else ""
        return Markup(f'<span class="nick rank-anon" title="익명">{escape(name)}{tail}</span>')
    key = rank_of(user, community)
    label, cls = RANKS[key]
    if key == "admin":  # 실제 닉네임은 비공개
        return Markup(f'<span class="nick {cls}" title="{label}">관리자</span>')
    return Markup(f'<span class="nick {cls}" title="{label}">{escape(user.nickname)}</span>')
