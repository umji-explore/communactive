"""커뮤콘: 유저가 직접 만들고, 포인트로 사고, 댓글에 쓰는 이모티콘"""
from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, url_for
from sqlalchemy.exc import IntegrityError

from models import ConItem, ConPack, UserConPack, db
from uploads import owned_upload
from utils import login_required, spend_points

bp = Blueprint("cons", __name__, url_prefix="/con")


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
        return redirect(url_for("cons.detail", pack_id=pack.id))
    have = g.user.points
    try:
        if pack.price and not spend_points(g.user, pack.price, "con_buy", f"커뮤콘 구매: {pack.name}"):
            db.session.rollback()
            flash(f"포인트가 부족합니다. ({have:,}P / {pack.price:,}P 필요)", "error")
            return redirect(url_for("cons.detail", pack_id=pack.id))
        db.session.add(UserConPack(user_id=g.user.id, pack_id=pack.id))
        pack.sales_count = ConPack.sales_count + 1
        db.session.commit()
        flash(f"'{pack.name}'을(를) 구매했습니다! 이제 댓글에서 쓸 수 있어요.", "success")
    except IntegrityError:  # 동시에 두 번 눌러서 이미 구매된 경우 → 전체 취소
        db.session.rollback()
        flash("이미 보유한 커뮤콘입니다.", "info")
    return redirect(url_for("cons.detail", pack_id=pack.id))


@bp.route("/new", methods=["GET", "POST"])
@login_required
def new():
    cfg = current_app.config
    form = {"name": "", "description": "", "price": "0"}
    if request.method == "POST":
        form = {k: request.form.get(k, "").strip() for k in form}
        # 이미지는 /upload 로 한 장씩 먼저 올리고, 여기서는 그 번호만 받는다 (Vercel 4.5MB 제한 대응)
        ids = list(dict.fromkeys(request.form.getlist("image_id", type=int)))
        uploads = [u for u in (owned_upload(i, g.user, "con") for i in ids) if u]
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
        elif not uploads:
            error = "이미지를 1개 이상 올려주세요."
        elif len(uploads) > cfg["CON_MAX_FILES"]:
            error = f"이미지는 최대 {cfg['CON_MAX_FILES']}개까지 올릴 수 있습니다."

        if error:
            flash(error, "error")
        else:
            pack = ConPack(creator_id=g.user.id, name=form["name"], description=form["description"], price=price)
            db.session.add(pack)
            db.session.flush()
            for i, up in enumerate(uploads):
                db.session.add(ConItem(pack_id=pack.id, path=up.path, sort_order=i))
            db.session.add(UserConPack(user_id=g.user.id, pack_id=pack.id))  # 만든 사람은 자동 보유
            db.session.commit()
            flash(f"커뮤콘 '{pack.name}'이(가) 등록되었습니다!", "success")
            return redirect(url_for("cons.detail", pack_id=pack.id))
    return render_template("con_new.html", form=form)
