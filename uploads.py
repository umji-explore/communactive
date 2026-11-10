"""이미지 업로드 (글 사진 · 커뮤콘 · 프로필 · 커뮤니티 아이콘/배너)

- 한 번에 한 장씩 올린다 (Vercel 은 요청 하나당 4.5MB 제한이 있어서).
- 확장자를 믿지 않고 실제 파일 내용으로 형식을 확인한다.
- Pillow 로 다시 저장해서 크기를 줄이고, 사진 속 위치정보(EXIF)를 지운다.
- 움직이는 GIF/WEBP 는 (글/커뮤콘만) 원본 그대로 저장한다.
"""
import io
import os
import uuid
from datetime import timedelta

from flask import Blueprint, current_app, g, jsonify, request, session
from PIL import Image, ImageOps

from models import Upload, db, utcnow
from utils import con_url

bp = Blueprint("uploads", __name__)

Image.MAX_IMAGE_PIXELS = 40_000_000  # 압축 폭탄 방지

# kind: (최대 변 길이, 정사각형으로 자르기, 움직이는 이미지 허용 최대 바이트 / 0이면 첫 장면만)
SPECS = {
    "post": (1600, False, 4_000_000),
    "con": (240, False, 1_000_000),
    "avatar": (240, True, 0),
    "icon": (160, True, 0),
    "banner": (1400, False, 0),
}
CONTENT_TYPES = {"gif": "image/gif", "webp": "image/webp", "png": "image/png", "jpg": "image/jpeg",
                 "mp4": "video/mp4", "webm": "video/webm", "mov": "video/quicktime"}
VIDEO_TYPES = {"video/mp4": "mp4", "video/webm": "webm", "video/quicktime": "mov"}
MAX_RAW = 4_400_000
UPLOADS_PER_HOUR = 120


class UploadError(Exception):
    pass


def sniff_image(head):
    """파일 앞부분(매직 넘버)으로 실제 이미지 형식을 확인"""
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    return None


def process(data, kind):
    """(저장할 바이트, 확장자, 가로, 세로) 를 돌려준다."""
    max_side, square, anim_max = SPECS[kind]
    fmt = sniff_image(data[:16])
    if not fmt:
        raise UploadError("PNG, JPG, GIF, WEBP 이미지만 올릴 수 있어요.")
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Image.DecompressionBombError:
        raise UploadError("이미지가 너무 커요.")
    except Exception:
        raise UploadError("이미지 파일이 손상되었어요.")

    if getattr(img, "is_animated", False) and anim_max:
        if len(data) > anim_max:
            raise UploadError(f"움직이는 이미지는 {anim_max // 1_000_000}MB 이하만 올릴 수 있어요.")
        return data, fmt, img.width, img.height

    img = ImageOps.exif_transpose(img)  # 휴대폰 사진 회전 정보 반영 (EXIF 는 다시 저장하며 사라짐)
    has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
    img = img.convert("RGBA" if has_alpha else "RGB")
    if square:
        img = ImageOps.fit(img, (max_side, max_side), method=Image.Resampling.LANCZOS)
    else:
        img.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    img.save(out, "WEBP", quality=85, method=4)
    return out.getvalue(), "webp", img.width, img.height


def store(data, ext, kind):
    """저장하고 경로(Blob URL 또는 static 기준 경로)를 돌려준다."""
    folder = {"con": "cons"}.get(kind, kind)  # "con" 은 윈도우 예약어(CON)라 폴더 이름으로 못 씀
    name = f"{folder}/{utcnow():%Y%m}/{uuid.uuid4().hex}.{ext}"
    if current_app.config["USE_BLOB"]:
        from vercel.blob.sync import put_blob
        return put_blob(f"uploads/{name}", data, access="public", content_type=CONTENT_TYPES[ext]).url
    path = os.path.join(current_app.config["UPLOAD_DIR"], *name.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return f"uploads/{name}"


def save_upload(user, file_storage, kind):
    if kind not in SPECS:
        raise UploadError("잘못된 요청입니다.")
    since = utcnow() - timedelta(hours=1)
    if Upload.query.filter(Upload.user_id == user.id, Upload.created_at >= since).count() >= UPLOADS_PER_HOUR:
        raise UploadError("사진을 너무 많이 올렸어요. 잠시 후 다시 시도해주세요.")
    data = file_storage.read(MAX_RAW + 1) if file_storage else b""
    if not data:
        raise UploadError("파일을 선택해주세요.")
    if len(data) > MAX_RAW:
        raise UploadError("파일이 너무 커요. (최대 4MB)")
    out, ext, w, h = process(data, kind)
    up = Upload(user_id=user.id, kind=kind, path=store(out, ext, kind), width=w, height=h)
    db.session.add(up)
    db.session.commit()
    return up


def owned_upload(upload_id, user, kind):
    """내가 올린 해당 종류의 업로드만 돌려준다."""
    if not upload_id:
        return None
    up = db.session.get(Upload, upload_id)
    if up and up.user_id == user.id and up.kind == kind:
        return up
    return None


def sniff_video(head):
    if head[4:8] == b"ftyp":
        return "mov" if head[8:10] == b"qt" else "mp4"
    if head[:4] == b"\x1a\x45\xdf\xa3":
        return "webm"
    return None


def _rate_limited(user):
    since = utcnow() - timedelta(hours=1)
    return Upload.query.filter(Upload.user_id == user.id, Upload.created_at >= since).count() >= UPLOADS_PER_HOUR


@bp.route("/upload", methods=["POST"])
def upload():
    if g.user is None:
        return jsonify(error="로그인이 필요합니다."), 401
    kind = request.form.get("kind", "")
    try:
        if kind == "video":  # Blob 이 없는 로컬 개발용: 4MB 이하 동영상은 서버로 바로
            if _rate_limited(g.user):
                raise UploadError("파일을 너무 많이 올렸어요. 잠시 후 다시 시도해주세요.")
            f = request.files.get("file")
            data = f.read(MAX_RAW + 1) if f else b""
            ext = sniff_video(data[:16])
            if not ext:
                raise UploadError("MP4, WEBM, MOV 동영상만 올릴 수 있어요.")
            if len(data) > MAX_RAW:
                raise UploadError("이 환경에서는 4MB 이하 동영상만 올릴 수 있어요.")
            up = Upload(user_id=g.user.id, kind="video", path=store(data, ext, "video"))
            db.session.add(up)
            db.session.commit()
        else:
            up = save_upload(g.user, request.files.get("file"), kind)
    except UploadError as e:
        return jsonify(error=str(e)), 400
    return jsonify(id=up.id, url=con_url(up.path), width=up.width, height=up.height)


# ---------- 동영상: 브라우저 → Vercel Blob 직접 업로드 ----------

@bp.route("/upload/video-token", methods=["POST"])
def video_token():
    """큰 동영상은 서버를 거치지 않고 브라우저가 Blob 에 바로 올린다. 서버는 짧은 수명의 토큰만 준다."""
    if g.user is None:
        return jsonify(error="로그인이 필요합니다."), 401
    cfg = current_app.config
    ctype = (request.form.get("type") or "").lower()
    size = request.form.get("size", type=int) or 0
    if ctype not in VIDEO_TYPES:
        return jsonify(error="MP4, WEBM, MOV 동영상만 올릴 수 있어요."), 400
    if size > cfg["VIDEO_MAX_BYTES"]:
        return jsonify(error=f"동영상은 {cfg['VIDEO_MAX_BYTES'] // (1024 * 1024)}MB 까지 올릴 수 있어요."), 400
    if _rate_limited(g.user):
        return jsonify(error="파일을 너무 많이 올렸어요. 잠시 후 다시 시도해주세요."), 429
    rw = os.environ.get("BLOB_READ_WRITE_TOKEN")
    if not (cfg["USE_BLOB"] and rw):
        return jsonify(mode="local")
    from blobtoken import generate_client_token
    pathname = f"uploads/video/{utcnow():%Y%m}/{uuid.uuid4().hex}.{VIDEO_TYPES[ctype]}"
    token = generate_client_token(rw, pathname, list(VIDEO_TYPES), cfg["VIDEO_MAX_BYTES"], valid_seconds=900)
    pending = session.get("video_paths", [])[-4:] + [pathname]
    session["video_paths"] = pending
    return jsonify(mode="blob", token=token, pathname=pathname)


@bp.route("/upload/video-done", methods=["POST"])
def video_done():
    """브라우저가 Blob 업로드를 마친 뒤 알려준다. 우리가 발급한 경로가 맞는지 확인하고 기록한다."""
    if g.user is None:
        return jsonify(error="로그인이 필요합니다."), 401
    from blobtoken import store_id_from_token
    pathname = request.form.get("pathname") or ""
    url = request.form.get("url") or ""
    store_id = store_id_from_token(os.environ.get("BLOB_READ_WRITE_TOKEN"))
    if pathname not in session.get("video_paths", []) or not store_id:
        return jsonify(error="잘못된 요청입니다."), 400
    if url != f"https://{store_id}.public.blob.vercel-storage.com/{pathname}":
        return jsonify(error="잘못된 요청입니다."), 400
    session["video_paths"] = [p for p in session.get("video_paths", []) if p != pathname]
    up = Upload(user_id=g.user.id, kind="video", path=url)
    db.session.add(up)
    db.session.commit()
    return jsonify(id=up.id, url=url)
