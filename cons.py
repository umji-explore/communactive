"""커뮤콘: 유저가 직접 만들고, 포인트로 사고, 댓글에 쓰는 이모티콘"""
import os
import shutil
import uuid

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, url_for

from models import ConItem, ConPack, UserConPack, db
from utils import add_points, login_required

bp = Blueprint("cons", __name__, url_prefix="/con")


def sniff_image(head):
    """파일 앞부분(매직 넘버)으로 실제 이미지 형식을 확인. 확장자는 믿지 않는다."""
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    return None


@bp.route("/")
def shop():
    sort = request.args.get("sort", "new")
    order = ConPack.sales_count.desc() if sort == "popular" else ConPack.id.desc()
    page = request.args.get("page", 1, type=int)
    pagination = ConPack.query.order_by(order, ConPack.id.desc()).paginate(page=page, per_page=24, error_out=False)
    owned = set()
    if g.user:
        owned = {up.pack_id for up in UserConPack.query.filter_by(user_id=g.user.id)}
    return render_template("con_shop.html", pagination=pagination, sort=sort, owned=owned)


@bp.route("/<int:pack_id>")
def detail(pack_id):
    pack = db.session.get(ConPack, pack_id) or abort(404)
    owned = bool(g.user and g.user.owns_pack(pack.id))
    return render_template("con_detail.html", pack=pack, owned=owned)


@bp.route("/<int:pack_id>/buy", methods=["POST"])
@login_required
def buy(pack_id):
    pack = db.session.get(ConPack, pack_id) or abort(404)
    if g.user.owns_pack(pack.id):
        flash("이미 보유한 커뮤콘입니다.", "info")
    elif g.user.points < pack.price:
        flash(f"포인트가 부족합니다. ({g.user.points:,}P / {pack.price:,}P 필요)", "error")
    else:
        if pack.price:
            add_points(g.user, -pack.price, "con_buy", f"커뮤콘 구매: {pack.name}")
        db.session.add(UserConPack(user_id=g.user.id, pack_id=pack.id))
        pack.sales_count += 1
        db.session.commit()
        flash(f"'{pack.name}'을(를) 구매했습니다! 이제 댓글에서 쓸 수 있어요.", "success")
    return redirect(url_for("cons.detail", pack_id=pack.id))


@bp.route("/new", methods=["GET", "POST"])
@login_required
def new():
    cfg = current_app.config
    form = {"name": "", "description": "", "price": "0"}
    if request.method == "POST":
        form = {k: request.form.get(k, "").strip() for k in form}
        files = [f for f in request.files.getlist("images") if f and f.filename]
        try:
            price = int(form["price"] or 0)
        except ValueError:
            price = -1

        error = None
        if not 1 <= len(form["name"]) <= 40:
            error = "커뮤콘 이름은 1~40자로 입력해주세요."
        elif len(form["description"]) > 300:
            error = "설명은 300자 이하로 입력해주세요."
        elif not 0 <= price <= cfg["CON_MAX_PRICE"]:
            error = f"가격은 0 ~ {cfg['CON_MAX_PRICE']:,}P 사이로 입력해주세요."
        elif not files:
            error = "이미지를 1개 이상 올려주세요."
        elif len(files) > cfg["CON_MAX_FILES"]:
            error = f"이미지는 최대 {cfg['CON_MAX_FILES']}개까지 올릴 수 있습니다."

        images = []
        if not error:
            for f in files:
                data = f.read(cfg["CON_MAX_FILE_SIZE"] + 1)
                if len(data) > cfg["CON_MAX_FILE_SIZE"]:
                    error = f"'{f.filename}' 파일이 너무 큽니다. (최대 {cfg['CON_MAX_FILE_SIZE'] // 1024}KB)"
                    break
                ext = sniff_image(data[:16])
                if not ext:
                    error = f"'{f.filename}'은(는) 지원하지 않는 형식입니다. (PNG, GIF, JPG, WEBP)"
                    break
                images.append((data, ext))

        if error:
            flash(error, "error")
        else:
            pack = ConPack(creator_id=g.user.id, name=form["name"], description=form["description"], price=price)
            db.session.add(pack)
            db.session.flush()
            folder = os.path.join(cfg["UPLOAD_DIR"], "cons", str(pack.id))
            os.makedirs(folder, exist_ok=True)
            try:
                for i, (data, ext) in enumerate(images):
                    name = f"{uuid.uuid4().hex}.{ext}"
                    with open(os.path.join(folder, name), "wb") as out:
                        out.write(data)
                    db.session.add(ConItem(pack_id=pack.id, path=f"uploads/cons/{pack.id}/{name}", sort_order=i))
                db.session.add(UserConPack(user_id=g.user.id, pack_id=pack.id))  # 만든 사람은 자동 보유
                db.session.commit()
            except Exception:
                db.session.rollback()
                shutil.rmtree(folder, ignore_errors=True)
                raise
            flash(f"커뮤콘 '{pack.name}'이(가) 등록되었습니다!", "success")
            return redirect(url_for("cons.detail", pack_id=pack.id))
    return render_template("con_new.html", form=form)
