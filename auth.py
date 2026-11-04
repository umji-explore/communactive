"""소셜 로그인 (구글 / 네이버 / 페이스북) + 회원가입 + 계정 연동"""
from authlib.integrations.flask_client import OAuth
from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, session, url_for

from models import PointLog, SocialAccount, User, UserConPack, db
from utils import NICKNAME_RE, login_required, safe_next

bp = Blueprint("auth", __name__)
RESERVED_NICK_PREFIXES = ("탈퇴회원", "관리자", "운영자")
oauth = OAuth()

PROVIDERS = {
    "google": {"label": "구글", "color": "google"},
    "naver": {"label": "네이버", "color": "naver"},
    "facebook": {"label": "페이스북", "color": "facebook"},
}


def init_oauth(app):
    oauth.init_app(app)
    c = app.config
    if c["GOOGLE_CLIENT_ID"]:
        oauth.register(
            "google",
            client_id=c["GOOGLE_CLIENT_ID"],
            client_secret=c["GOOGLE_CLIENT_SECRET"],
            server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
            client_kwargs={"scope": "openid email profile"},
        )
    if c["NAVER_CLIENT_ID"]:
        oauth.register(
            "naver",
            client_id=c["NAVER_CLIENT_ID"],
            client_secret=c["NAVER_CLIENT_SECRET"],
            authorize_url="https://nid.naver.com/oauth2.0/authorize",
            access_token_url="https://nid.naver.com/oauth2.0/token",
            api_base_url="https://openapi.naver.com/",
            client_kwargs={"token_endpoint_auth_method": "client_secret_post"},
        )
    if c["FACEBOOK_CLIENT_ID"]:
        oauth.register(
            "facebook",
            client_id=c["FACEBOOK_CLIENT_ID"],
            client_secret=c["FACEBOOK_CLIENT_SECRET"],
            authorize_url="https://www.facebook.com/v21.0/dialog/oauth",
            access_token_url="https://graph.facebook.com/v21.0/oauth/access_token",
            api_base_url="https://graph.facebook.com/v21.0/",
            client_kwargs={"scope": "email public_profile", "token_endpoint_auth_method": "client_secret_post"},
        )


def enabled_providers():
    return [p for p in PROVIDERS if oauth.create_client(p) is not None]


def fetch_profile(provider, client, token):
    """각 서비스에서 (고유ID, 이메일, 이름)을 가져온다."""
    if provider == "google":
        info = token.get("userinfo") or client.userinfo()
        return str(info["sub"]), info.get("email"), info.get("name")
    if provider == "naver":
        data = client.get("v1/nid/me", token=token).json()
        info = data.get("response") or {}
        return str(info["id"]), info.get("email"), info.get("nickname") or info.get("name")
    if provider == "facebook":
        info = client.get("me", params={"fields": "id,name,email"}, token=token).json()
        return str(info["id"]), info.get("email"), info.get("name")
    raise ValueError(provider)


def login_user(user):
    session.clear()
    session["uid"] = user.id
    session.permanent = True


# ---------- 로그인 ----------

@bp.route("/login")
def login():
    if g.user:
        return redirect(url_for("main.home"))
    session["next"] = safe_next(request.args.get("next"))
    return render_template(
        "login.html",
        providers=PROVIDERS,
        enabled=enabled_providers(),
        dev_login=current_app.config["DEV_LOGIN"],
    )


@bp.route("/login/<provider>")
def oauth_start(provider):
    client = oauth.create_client(provider) if provider in PROVIDERS else None
    if client is None:
        flash(f"{PROVIDERS.get(provider, {}).get('label', provider)} 로그인이 아직 설정되지 않았습니다.", "error")
        return redirect(url_for("auth.login"))
    session["oauth_intent"] = "link" if (request.args.get("link") and g.user) else "login"
    redirect_uri = url_for("auth.oauth_callback", provider=provider, _external=True)
    return client.authorize_redirect(redirect_uri)


@bp.route("/auth/<provider>/callback")
def oauth_callback(provider):
    client = oauth.create_client(provider) if provider in PROVIDERS else None
    if client is None:
        abort(404)
    try:
        token = client.authorize_access_token()
        uid, email, name = fetch_profile(provider, client, token)
    except Exception as e:  # 사용자가 취소했거나 토큰 오류
        current_app.logger.warning("OAuth 실패 (%s): %s", provider, e)
        flash("로그인에 실패했습니다. 다시 시도해주세요.", "error")
        return redirect(url_for("auth.login"))

    label = PROVIDERS[provider]["label"]
    account = SocialAccount.query.filter_by(provider=provider, provider_uid=uid).first()

    # 마이페이지에서 "연동하기"를 누른 경우
    if session.pop("oauth_intent", "login") == "link" and g.user:
        if account and account.user_id != g.user.id:
            flash(f"이 {label} 계정은 이미 다른 회원과 연동되어 있습니다.", "error")
        elif account:
            flash(f"이미 연동된 {label} 계정입니다.", "info")
        elif any(a.provider == provider for a in g.user.social_accounts):
            flash(f"이미 다른 {label} 계정이 연동되어 있습니다.", "error")
        else:
            db.session.add(SocialAccount(user_id=g.user.id, provider=provider, provider_uid=uid, email=email))
            db.session.commit()
            flash(f"{label} 계정이 연동되었습니다.", "success")
        return redirect(url_for("main.mypage"))

    if account:
        next_url = session.get("next")
        login_user(account.user)
        flash(f"{account.user.nickname}님, 환영합니다!", "success")
        return redirect(safe_next(next_url))

    # 처음 온 사람 → 회원가입(닉네임 설정)
    session["pending_signup"] = {"provider": provider, "uid": uid, "email": email, "name": name or ""}
    return redirect(url_for("auth.signup"))


# ---------- 회원가입 ----------

@bp.route("/signup", methods=["GET", "POST"])
def signup():
    pending = session.get("pending_signup")
    if not pending:
        return redirect(url_for("auth.login"))

    suggested = "".join(ch for ch in pending.get("name", "") if NICKNAME_RE.match(ch * 2))[:12]
    error = None
    if request.method == "POST":
        nickname = request.form.get("nickname", "").strip()
        if not request.form.get("agree"):
            error = "이용약관에 동의해주세요."
        elif not NICKNAME_RE.match(nickname):
            error = "닉네임은 2~12자의 한글, 영문, 숫자, _ 만 쓸 수 있습니다."
        elif nickname.startswith(RESERVED_NICK_PREFIXES) or nickname.lower() in {"admin", "communactive"}:
            error = "사용할 수 없는 닉네임입니다."
        elif User.query.filter(db.func.lower(User.nickname) == nickname.lower()).first():
            error = "이미 사용 중인 닉네임입니다."
        elif SocialAccount.query.filter_by(provider=pending["provider"], provider_uid=pending["uid"]).first():
            error = "이미 가입된 계정입니다. 다시 로그인해주세요."
        else:
            user = User(nickname=nickname, email=pending.get("email"))
            db.session.add(user)
            db.session.flush()
            db.session.add(SocialAccount(
                user_id=user.id, provider=pending["provider"],
                provider_uid=pending["uid"], email=pending.get("email"),
            ))
            db.session.commit()
            next_url = session.get("next")
            login_user(user)
            flash(f"가입을 환영합니다, {nickname}님! 글을 쓰면 {current_app.config['POINTS_POST']}P, "
                  f"댓글을 달면 {current_app.config['POINTS_COMMENT']}P를 받아요.", "success")
            return redirect(safe_next(next_url))
        suggested = nickname

    label = PROVIDERS.get(pending["provider"], {}).get("label", "개발용")
    return render_template("signup.html", pending=pending, provider_label=label, suggested=suggested, error=error)


# ---------- 개발용 로그인 (DEV_LOGIN=1 일 때만) ----------

@bp.route("/dev-login", methods=["POST"])
def dev_login():
    if not current_app.config["DEV_LOGIN"]:
        abort(404)
    nickname = request.form.get("nickname", "").strip()
    if not NICKNAME_RE.match(nickname):
        flash("닉네임은 2~12자의 한글, 영문, 숫자, _ 만 쓸 수 있습니다.", "error")
        return redirect(url_for("auth.login"))
    account = SocialAccount.query.filter_by(provider="dev", provider_uid=nickname).first()
    if account:
        next_url = session.get("next")
        login_user(account.user)
        return redirect(safe_next(next_url))
    session["pending_signup"] = {"provider": "dev", "uid": nickname, "email": None, "name": nickname}
    return redirect(url_for("auth.signup"))


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("로그아웃되었습니다.", "info")
    return redirect(url_for("main.home"))


@bp.route("/me/withdraw", methods=["POST"])
@login_required
def withdraw():
    """회원 탈퇴: 개인정보는 지우고, 글/댓글은 '탈퇴회원'으로 남긴다."""
    if request.form.get("confirm") != g.user.nickname:
        flash("확인을 위해 닉네임을 정확히 입력해주세요.", "error")
        return redirect(url_for("main.mypage"))
    user = g.user
    for acc in list(user.social_accounts):
        db.session.delete(acc)
    PointLog.query.filter_by(user_id=user.id).delete()
    UserConPack.query.filter_by(user_id=user.id).delete()
    user.nickname = f"탈퇴회원{user.id}"
    user.email = None
    user.points = 0
    user.is_admin = False
    db.session.commit()
    session.clear()
    flash("탈퇴가 완료되었습니다. 그동안 이용해주셔서 감사합니다.", "info")
    return redirect(url_for("main.home"))


@bp.route("/me/unlink/<provider>", methods=["POST"])
@login_required
def unlink(provider):
    accounts = g.user.social_accounts
    if len(accounts) <= 1:
        flash("마지막 남은 연동 계정은 해제할 수 없습니다.", "error")
    else:
        acc = next((a for a in accounts if a.provider == provider), None)
        if acc:
            db.session.delete(acc)
            db.session.commit()
            flash(f"{PROVIDERS.get(provider, {}).get('label', provider)} 연동이 해제되었습니다.", "info")
    return redirect(url_for("main.mypage"))
