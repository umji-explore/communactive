"""보안 코드 (자동입력 방지 그림 글자)

글자를 비틀고 선·점을 섞은 PNG 이미지를 만들고, 정답은 세션에 해시로만 저장한다.
한 번 확인하면 (맞든 틀리든) 바로 폐기해서 같은 코드를 재사용할 수 없다.
"""
import hashlib
import hmac
import io
import math
import random
import secrets
import time

from flask import Blueprint, Response, current_app, session
from PIL import Image, ImageDraw, ImageFilter, ImageFont

bp = Blueprint("captcha", __name__)

CHARS = "ABCDEFGHJKLNPQRSTUVXYZ23456789"  # 헷갈리는 0/O, 1/I 와 너무 넓은 M/W 제외
LENGTH = 5
TTL = 300  # 5분
W, H = 170, 56


def _digest(answer):
    key = current_app.config["SECRET_KEY"].encode()
    return hmac.new(key, answer.upper().encode(), hashlib.sha256).hexdigest()


def _render(text):
    rnd = random.SystemRandom()
    img = Image.new("RGB", (W, H), (245, 245, 245))
    d = ImageDraw.Draw(img)
    # 배경 잡선
    for _ in range(6):
        d.line([(rnd.randint(0, W), rnd.randint(0, H)), (rnd.randint(0, W), rnd.randint(0, H))],
               fill=tuple(rnd.randint(150, 210) for _ in range(3)), width=1)
    # 글자: 하나씩 회전시켜 붙인다
    glyphs = []
    for ch in text:
        font = ImageFont.load_default(size=rnd.randint(32, 36))
        l, t, r, b = font.getbbox(ch)
        glyph = Image.new("RGBA", (r - l + 12, b - t + 12), (0, 0, 0, 0))
        ImageDraw.Draw(glyph).text((6 - l, 6 - t), ch, font=font,
                                   fill=tuple(rnd.randint(20, 80) for _ in range(3)) + (255,))
        glyphs.append(glyph.rotate(rnd.uniform(-22, 22), resample=Image.Resampling.BICUBIC, expand=True))
    widths = sum(gl.width for gl in glyphs)
    overlap = max(13, -(-(widths - (W - 8)) // (len(glyphs) - 1)))  # 넓은 글자면 더 겹쳐서 잘리지 않게
    total = widths - overlap * (len(glyphs) - 1)
    x = max(2, (W - total) // 2)
    for gl in glyphs:
        y = (H - gl.height) // 2 + rnd.randint(-3, 3)
        img.paste(gl, (x, y), gl)
        x += gl.width - overlap
    # 물결 왜곡
    amp, period, phase = rnd.uniform(2, 4), rnd.uniform(28, 44), rnd.uniform(0, 6.28)
    src = img.copy()
    px, sp = img.load(), src.load()
    for i in range(W):
        dy = int(amp * math.sin(i / period * 6.28 + phase))
        for j in range(H):
            jj = j + dy
            px[i, j] = sp[i, jj] if 0 <= jj < H else (245, 245, 245)
    # 앞쪽 방해선 + 점
    d = ImageDraw.Draw(img)
    y1, y2 = rnd.randint(16, H - 16), rnd.randint(16, H - 16)
    d.line([(0, y1), (W, y2)], fill=(110, 110, 110), width=1)
    for _ in range(140):
        d.point((rnd.randint(0, W - 1), rnd.randint(0, H - 1)), fill=tuple(rnd.randint(60, 180) for _ in range(3)))
    img = img.filter(ImageFilter.SMOOTH)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


@bp.route("/captcha.png")
def image():
    text = "".join(secrets.choice(CHARS) for _ in range(LENGTH))
    session["captcha"] = {"h": _digest(text), "t": int(time.time())}
    resp = Response(_render(text), mimetype="image/png")
    resp.headers["Cache-Control"] = "no-store, max-age=0"
    return resp


def check(answer):
    """맞으면 True. 한 번 확인한 코드는 폐기한다."""
    data = session.pop("captcha", None)
    if not data or not answer or time.time() - data.get("t", 0) > TTL:
        return False
    return hmac.compare_digest(data["h"], _digest(answer.strip().replace(" ", "")))
