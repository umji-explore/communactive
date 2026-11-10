"""글 본문 (에디터 HTML) 정리·검사·표시

저장할 때
  - nh3 로 허용한 태그/속성/스타일만 남긴다 (스크립트, 이벤트 속성, javascript: 링크 등은 전부 제거).
  - 사진·동영상·커뮤콘·유튜브·외부 콘텐츠는 <figure data-embed="종류" data-id="번호"></figure> 자리표시로만 저장한다.
    figure 안에 사용자가 넣은 내용은 버리고, 보여줄 때 DB 에서 다시 만든다 → 남의 사진을 끼워 넣거나 HTML 을 숨길 수 없다.
보여줄 때
  - 자리표시를 실제 <img>/<video>/<iframe>/카드로 바꾼다.
"""
import html as htmllib
import re

import nh3
from markupsafe import Markup, escape

from models import ConItem, LinkPreview, Upload, UserConPack
from utils import IMG_TOKEN_RE, YOUTUBE_RE, con_url, linkify

TAGS = {"p", "div", "br", "b", "strong", "i", "em", "u", "s", "strike", "span", "blockquote", "hr", "a", "figure"}
ATTRS = {
    "a": {"href"},
    "span": {"style"}, "p": {"style"}, "div": {"style"}, "b": {"style"}, "i": {"style"},
    "figure": {"data-embed", "data-id"},
}
STYLES = {"color", "background-color", "font-size", "font-weight", "font-style",
          "text-decoration", "text-decoration-line", "text-align"}
EMBED_KINDS = {"image", "video", "con", "youtube", "vimeo", "link"}
FIGURE_RE = re.compile(r"<figure\b([^>]*)>.*?</figure>", re.S | re.I)
ATTR_RE = re.compile(r'data-(embed|id)="([^"]*)"')
ID_RE = {"youtube": re.compile(r"^[A-Za-z0-9_-]{11}$"), "vimeo": re.compile(r"^\d{1,12}$")}
MAX_HTML = 100_000
MAX_EMBEDS = 50


def _figure_attrs(attr_text):
    a = dict(ATTR_RE.findall(attr_text))
    return a.get("embed", ""), a.get("id", "")


def clean_html(raw):
    return nh3.clean(raw or "", tags=TAGS, attributes=ATTRS, url_schemes={"http", "https"},
                     filter_style_properties=STYLES, link_rel="noopener nofollow ugc", strip_comments=True,
                     set_tag_attribute_values={"a": {"target": "_blank"}})


def html_to_text(h):
    h = FIGURE_RE.sub(" ", h or "")
    h = re.sub(r"(?i)<br\s*/?>|</(p|div|blockquote)>", "\n", h)
    t = htmllib.unescape(re.sub(r"<[^>]+>", "", h))
    return re.sub(r"\n{3,}", "\n\n", t).strip()


class ContentError(Exception):
    pass


def prepare(raw, user, max_images=30):
    """에디터에서 온 HTML → (저장할 HTML, 순수 글자, 사진 있음, 동영상 있음)"""
    if len(raw or "") > MAX_HTML * 2:
        raise ContentError("글이 너무 길어요.")
    cleaned = clean_html(raw)
    found = [(m, *_figure_attrs(m.group(1))) for m in FIGURE_RE.finditer(cleaned)]
    if len(found) > MAX_EMBEDS:
        raise ContentError(f"사진·동영상 등은 글 하나에 {MAX_EMBEDS}개까지 넣을 수 있어요.")

    def ids(kind):
        return {int(i) for _, k, i in found if k == kind and i.isdigit()}

    up_ids = ids("image") | ids("video")
    uploads = {u.id: u for u in Upload.query.filter(Upload.id.in_(up_ids), Upload.user_id == user.id)} if up_ids else {}
    owned_packs = {r.pack_id for r in UserConPack.query.filter_by(user_id=user.id)}
    cons = {c.id: c for c in ConItem.query.filter(ConItem.id.in_(ids("con")))} if ids("con") else {}
    links = {lp.id for lp in LinkPreview.query.filter(LinkPreview.id.in_(ids("link")))} if ids("link") else set()

    has_image = has_video = False
    n_images = 0

    def keep(m):
        nonlocal has_image, has_video, n_images
        kind, ident = _figure_attrs(m.group(1))
        ok = False
        if kind == "image" and ident.isdigit():
            u = uploads.get(int(ident))
            ok = bool(u and u.kind in ("post", "ai"))
            if ok:
                has_image, n_images = True, n_images + 1
        elif kind == "video" and ident.isdigit():
            u = uploads.get(int(ident))
            ok = bool(u and u.kind == "video")
            has_video = has_video or ok
        elif kind == "con" and ident.isdigit():
            c = cons.get(int(ident))
            ok = bool(c and c.pack_id in owned_packs)
        elif kind in ID_RE:
            ok = bool(ID_RE[kind].match(ident))
            has_video = has_video or ok
        elif kind == "link" and ident.isdigit():
            ok = int(ident) in links
        return f'<figure data-embed="{kind}" data-id="{ident}"></figure>' if ok else ""

    stored = FIGURE_RE.sub(keep, cleaned).strip()
    if n_images > max_images:
        raise ContentError(f"사진은 글 하나에 {max_images}장까지 넣을 수 있어요.")
    if len(stored) > MAX_HTML:
        raise ContentError("글이 너무 길어요.")
    text = html_to_text(stored)
    if len(text) > 20000:
        raise ContentError("내용은 20,000자 이하로 입력해주세요.")
    if not text and "<figure" not in stored:
        raise ContentError("내용을 입력해주세요.")
    return stored, text, has_image, has_video


def _render_figure(kind, ident, ctx, editing):
    ce = ' contenteditable="false"' if editing else ""
    head = f'<figure class="emb emb-{kind}" data-embed="{kind}" data-id="{ident}"{ce}>'
    rm = '<button type="button" class="emb-remove" title="빼기">✕</button>' if editing else ""
    if kind in ("image", "video"):
        u = ctx["uploads"].get(int(ident))
        if not u:
            return ""
        src = escape(con_url(u.path))
        if kind == "video":
            body = f'<video src="{src}" controls preload="metadata" playsinline></video>'
        else:
            size = f' width="{u.width}" height="{u.height}"' if u.width else ""
            img = f'<img src="{src}"{size} loading="lazy" alt="">'
            body = img if editing else f'<a href="{src}" target="_blank" rel="noopener">{img}</a>'
            if u.kind == "ai":
                body += '<figcaption>AI 생성 이미지</figcaption>'
        return head + body + rm + "</figure>"
    if kind == "con":
        c = ctx["cons"].get(int(ident))
        return head + f'<img src="{escape(con_url(c.path))}" alt="커뮤콘">' + rm + "</figure>" if c else ""
    if kind == "youtube":
        src = f"https://www.youtube-nocookie.com/embed/{ident}"
    elif kind == "vimeo":
        src = f"https://player.vimeo.com/video/{ident}"
    else:
        src = None
    if src:
        if editing:  # 편집 중엔 iframe 대신 가벼운 표시 (클릭/선택이 iframe 에 먹히지 않게)
            label = "유튜브" if kind == "youtube" else "Vimeo"
            thumb = f'<img src="https://i.ytimg.com/vi/{ident}/hqdefault.jpg" alt="" referrerpolicy="no-referrer">' if kind == "youtube" else ""
            return head + f'<div class="emb-video-ph">{thumb}<span>▶ {label} 동영상</span></div>' + rm + "</figure>"
        return head + (f'<div class="emb-frame"><iframe src="{src}" title="동영상" loading="lazy" allowfullscreen '
                       'allow="accelerometer; encrypted-media; gyroscope; picture-in-picture"></iframe></div>') + "</figure>"
    if kind == "link":
        lp = ctx["links"].get(int(ident))
        if not lp:
            return ""
        img = (f'<img src="{escape(lp.image)}" alt="" loading="lazy" referrerpolicy="no-referrer">' if lp.image else "")
        tag = "div" if editing else "a"
        attrs = "" if editing else f' href="{escape(lp.url)}" target="_blank" rel="noopener nofollow ugc"'
        return (head + f'<{tag} class="link-card"{attrs}>{img}<span class="lc-text">'
                f'<strong>{escape(lp.title or lp.url)}</strong>'
                f'<span class="lc-desc">{escape(lp.description)}</span>'
                f'<span class="lc-site">{escape(lp.site)}</span></span></{tag}>' + rm + "</figure>")
    return ""


def render(stored, author_id, editing=False):
    """저장된 HTML → 보여줄 HTML (editing=True 면 에디터용)"""
    found = [_figure_attrs(m.group(1)) for m in FIGURE_RE.finditer(stored or "")]

    def ids(*kinds):
        return {int(i) for k, i in found if k in kinds and i.isdigit()}

    ctx = {
        "uploads": {u.id: u for u in Upload.query.filter(Upload.id.in_(ids("image", "video")), Upload.user_id == author_id)}
        if ids("image", "video") else {},
        "cons": {c.id: c for c in ConItem.query.filter(ConItem.id.in_(ids("con")))} if ids("con") else {},
        "links": {lp.id: lp for lp in LinkPreview.query.filter(LinkPreview.id.in_(ids("link")))} if ids("link") else {},
    }
    out = FIGURE_RE.sub(lambda m: _render_figure(*_figure_attrs(m.group(1)), ctx, editing), stored or "")
    return Markup(out)


def legacy_to_html(content):
    """예전 글(텍스트 + [[img:번호]]) → 에디터 HTML (수정할 때 사용)"""
    parts = []
    for raw in (content or "").split("\n"):
        line = raw.strip()
        m = IMG_TOKEN_RE.match(line)
        y = YOUTUBE_RE.match(line)
        if m:
            parts.append(f'<figure data-embed="image" data-id="{m.group(1)}"></figure>')
        elif y:
            parts.append(f'<figure data-embed="youtube" data-id="{y.group(1)}"></figure>')
        else:
            parts.append(f"<div>{linkify(raw) or '<br>'}</div>")
    return "".join(parts)
