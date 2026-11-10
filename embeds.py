"""외부 콘텐츠 (링크 카드 / 유튜브 / Vimeo)

링크 카드를 만들려고 서버가 남의 사이트에 접속하므로, 서버 내부망이나 클라우드 메타데이터 주소로
접속하게 만드는 공격(SSRF)을 막는다.
  - http/https, 80/443 포트만
  - 도메인을 IP 로 바꾼 뒤 공인 IP 인지 확인하고, 확인한 그 IP 로 직접 접속 (DNS 를 바꿔치기 해도 소용없음)
  - 리다이렉트는 3번까지, 매번 같은 검사
  - 시간 제한 / 받는 크기 제한 (512KB)
"""
import ipaddress
import re
import socket
from datetime import timedelta
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import certifi
import urllib3
from flask import Blueprint, g, jsonify, request

from models import LinkPreview, db, utcnow
from utils import YOUTUBE_RE

bp = Blueprint("embeds", __name__)

VIMEO_RE = re.compile(r"^https?://(?:www\.)?vimeo\.com/(\d{1,12})(?:[/?#]\S*)?$")
MAX_BYTES = 512 * 1024
PREVIEWS_PER_HOUR = 40
UA = "Mozilla/5.0 (compatible; communactiveBot/1.0; +https://communactive.vercel.app)"


class FetchError(Exception):
    pass


def _public_ip(host, port):
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise FetchError("주소를 찾을 수 없어요.")
    ips = []
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global or ip.is_multicast or ip.is_reserved:
            raise FetchError("이 주소는 불러올 수 없어요.")
        ips.append(str(ip))
    if not ips:
        raise FetchError("주소를 찾을 수 없어요.")
    return ips[0]


def fetch_html(url):
    """안전하게 HTML 을 받아 (최종 URL, 본문 문자열) 을 돌려준다."""
    for _ in range(4):
        p = urlparse(url)
        if p.scheme not in ("http", "https") or not p.hostname:
            raise FetchError("http 또는 https 주소만 넣을 수 있어요.")
        default_port = 443 if p.scheme == "https" else 80
        port = p.port or default_port
        if port != default_port:
            raise FetchError("이 주소는 불러올 수 없어요.")
        ip = _public_ip(p.hostname, port)
        timeout = urllib3.Timeout(connect=3, read=5)
        if p.scheme == "https":
            pool = urllib3.HTTPSConnectionPool(ip, port, server_hostname=p.hostname, assert_hostname=p.hostname,
                                               cert_reqs="CERT_REQUIRED", ca_certs=certifi.where(),
                                               timeout=timeout, retries=False, maxsize=1)
        else:
            pool = urllib3.HTTPConnectionPool(ip, port, timeout=timeout, retries=False, maxsize=1)
        path = (p.path or "/") + (f"?{p.query}" if p.query else "")
        try:
            r = pool.urlopen("GET", path, headers={"Host": p.hostname, "User-Agent": UA,
                                                   "Accept": "text/html,application/xhtml+xml",
                                                   "Accept-Language": "ko,en;q=0.8"},
                             redirect=False, preload_content=False)
        except Exception:
            raise FetchError("사이트에 접속하지 못했어요.")
        try:
            if r.status in (301, 302, 303, 307, 308) and r.headers.get("Location"):
                url = urljoin(url, r.headers["Location"])
                continue
            if r.status != 200:
                raise FetchError("사이트에서 내용을 받지 못했어요.")
            if "html" not in (r.headers.get("Content-Type") or "").lower():
                raise FetchError("웹페이지 주소만 카드로 만들 수 있어요.")
            body = r.read(MAX_BYTES, decode_content=True)
            m = re.search(r"charset=([\w-]+)", r.headers.get("Content-Type") or "", re.I)
            enc = m.group(1) if m else "utf-8"
            try:
                return url, body.decode(enc, errors="replace")
            except LookupError:
                return url, body.decode("utf-8", errors="replace")
        finally:
            r.release_conn()
    raise FetchError("너무 여러 번 다른 주소로 이동해요.")


class _MetaParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.meta, self.title, self._in_title = {}, "", False

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "meta":
            key = (a.get("property") or a.get("name") or "").lower()
            if key and "content" in a and key not in self.meta:
                self.meta[key] = a["content"].strip()
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title and len(self.title) < 300:
            self.title += data


def preview(url):
    final, body = fetch_html(url)
    p = _MetaParser()
    try:
        p.feed(body)
    except Exception:
        pass
    m = p.meta
    image = urljoin(final, m.get("og:image") or m.get("twitter:image") or "")
    if urlparse(image).scheme not in ("http", "https"):
        image = ""
    return {
        "url": url,
        "title": (m.get("og:title") or m.get("twitter:title") or p.title or url).strip()[:200],
        "description": (m.get("og:description") or m.get("description") or "").strip()[:300],
        "image": image[:1000],
        "site": (m.get("og:site_name") or urlparse(final).hostname or "")[:100],
    }


@bp.route("/embed", methods=["POST"])
def embed():
    if g.user is None:
        return jsonify(error="로그인이 필요합니다."), 401
    url = (request.form.get("url") or "").strip()
    if len(url) > 1000:
        return jsonify(error="주소가 너무 길어요."), 400
    y = YOUTUBE_RE.match(url)
    if y:
        return jsonify(embed="youtube", id=y.group(1))
    v = VIMEO_RE.match(url)
    if v:
        return jsonify(embed="vimeo", id=v.group(1))
    since = utcnow() - timedelta(hours=1)
    if LinkPreview.query.filter(LinkPreview.user_id == g.user.id, LinkPreview.created_at >= since).count() >= PREVIEWS_PER_HOUR:
        return jsonify(error="잠시 후 다시 시도해주세요."), 429
    try:
        data = preview(url)
    except FetchError as e:
        return jsonify(error=str(e)), 400
    lp = LinkPreview(user_id=g.user.id, **data)
    db.session.add(lp)
    db.session.commit()
    return jsonify(embed="link", id=lp.id, title=lp.title)
