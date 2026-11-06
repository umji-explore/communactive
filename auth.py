"""이메일 회원가입 (gmail.com / naver.com 만) · 로그인 · 비밀번호 찾기 · 탈퇴"""
import hashlib
import hmac
import re
import secrets
from datetime import timedelta

from flask import Blueprint, current_app, flash, g, redirect, render_template, request, session, url_for
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash

from mailer import MailNotConfigured, send_code
from models import EmailCode, PointLog, User, UserConPack, db, utcnow
from utils import NICKNAME_RE, login_required, safe_next

bp = Blueprint("auth", __name__)

RESERVED_NICK_PREFIXES = ("탈퇴회원", "관리자", "운영자")
EMAIL_RE = re.compile(r"^[a-z0-9._-]{1,64}@([a-z0-9.-]+)$")
MAX_CODE_ATTEMPTS = 5
MAX_LOGIN_FAILS = 5
LOCK_MINUTES = 10
_DUMMY_HASH = generate_password_hash("dummy-password-for-timing")


# ---------- 공통 ----------

def normalize_email(raw):
    """허용 도메인인지 확인하고 표준 형태로 바꾼다. 안 되면 (None, 오류메시지)."""
    email = (raw or "").strip().lower()
    m = EMAIL_RE.match(email)
    domains = current_app.config["ALLOWED_EMAIL_DOMAINS"]
    if not m:
        return None, "올바른 이메일 주소를 입력해주세요. (+ 기호는 쓸 수 없어요)"
    if m.group(1) not in domains:
        return None, "gmail.com 또는 naver.com 이메일로만 가입할 수 있어요."
    local, domain = email.split("@")
    if domain == "gmail.com":
        local = local.replace(".", "")  # Gmail은 점(.)을 무시하므로 같은 주소로 취급
    return f"{local}@{domain}", None


def validate_password(pw, pw2):
    if pw != pw2:
        return "비밀번호 확인이 일치하지 않습니다."
    if not 8 <= len(pw) <= 64:
        return "비밀번호는 8~64자로 입력해주세요."
    if not (re.search(r"[A-Za-z]", pw) and re.search(r"\d", pw)):
        return "비밀번호에 영문과 숫자를 모두 넣어주세요."
    return None


def validate_nickname(nickname):
    if not NICKNAME_RE.match(nickname):
        return "닉네임은 2~12자의 한글, 영문, 숫자, _ 만 쓸 수 있습니다."
    if nickname.startswith(RESERVED_NICK_PREFIXES) or nickname.lower() in {"admin", "communactive"}:
        return "사용할 수 없는 닉네임입니다."
    if User.query.filter(db.func.lower(User.nickname) == nickname.lower()).first():
        return "이미 사용 중인 닉네임입니다."
    return None


def _code_digest(email, purpose, code):
    key = current_app.config["SECRET_KEY"].encode()
    return hmac.new(key, f"{purpose}:{email}:{code}".encode(), hashlib.sha256).hexdigest()


def issue_code(email, purpose):
    """인증번호 발송. 성공하면 None, 실패하면 오류 메시지."""
    cfg = current_app.config
    now = utcnow()
    ip = request.remote_addr or ""
    last = (EmailCode.query.filter_by(email=email, purpose=purpose)
            .order_by(EmailCode.id.desc()).first())
    if last and (now - last.created_at).total_seconds() < cfg["EMAIL_CODE_RESEND"]:
        wait = cfg["EMAIL_CODE_RESEND"] - int((now - last.created_at).total_seconds())
        return f"인증번호는 {wait}초 후에 다시 받을 수 있어요."
    recent_from_ip = EmailCode.query.filter(EmailCode.ip == ip, EmailCode.created_at >= now - timedelta(hours=1)).count()
    if recent_from_ip >= 10:
        return "인증 요청이 너무 많아요. 잠시 후 다시 시도해주세요."

    code = f"{secrets.randbelow(10 ** 6):06d}"
    try:
        send_code(email, code, purpose)
    except MailNotConfigured:
        return "메일 발송이 아직 설정되지 않았어요. 운영자에게 문의해주세요."
    except Exception as e:  # SMTP 오류
        current_app.logger.error("메일 발송 실패: %s", e)
        return "인증 메일을 보내지 못했어요. 잠시 후 다시 시도해주세요."

    EmailCode.query.filter(EmailCode.created_at < now - timedelta(days=1)).delete()
    db.session.add(EmailCode(email=email, purpose=purpose, ip=ip, code_hash=_code_digest(email, purpose, code),
                             expires_at=now + timedelta(seconds=cfg["EMAIL_CODE_TTL"])))
    db.session.commit()
    if cfg["MAIL_DEBUG_ECHO"]:
        flash(f"[개발 모드] 메일 설정이 없어 인증번호를 화면에 표시합니다: {code}", "info")
    return None


def check_code(email, purpose, code):
    """인증번호 확인. 성공하면 None, 실패하면 오류 메시지."""
    row = (EmailCode.query.filter_by(email=email, purpose=purpose)
           .order_by(EmailCode.id.desc()).first())
    if not row or row.expires_at < utcnow():
        return "인증번호가 만료되었어요. 다시 받아주세요."
    if row.attempts >= MAX_CODE_ATTEMPTS:
        return "인증번호를 너무 많이 틀렸어요. 다시 받아주세요."
    if not hmac.compare_digest(row.code_hash, _code_digest(email, purpose, (code or "").strip())):
        row.attempts += 1
        db.session.commit()
        return f"인증번호가 올바르지 않아요. ({MAX_CODE_ATTEMPTS - row.attempts}번 남음)"
    db.session.delete(row)
    return None


def login_user(user):
    session.clear()
    session["uid"] = user.id
    session.permanent = True


# ---------- 로그인 ----------

@bp.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("main.home"))
    next_url = safe_next(request.values.get("next"))
    email = ""
    if request.method == "POST":
        email, _ = normalize_email(request.form.get("email"))
        email = email or (request.form.get("email") or "").strip().lower()
        password = request.form.get("password", "")
        user = User.query.filter_by(email=email).first()
        now = utcnow()
        if user and user.locked_until and user.locked_until > now:
            mins = int((user.locked_until - now).total_seconds() // 60) + 1
            flash(f"로그인을 여러 번 실패해서 잠겼어요. {mins}분 후에 다시 시도해주세요.", "error")
        elif user and user.password_hash and check_password_hash(user.password_hash, password):
            user.failed_logins, user.locked_until = 0, None
            db.session.commit()
            login_user(user)
            return redirect(next_url)
        else:
            if not user:
                check_password_hash(_DUMMY_HASH, password)  # 계정 존재 여부가 응답 시간으로 드러나지 않게
            else:
                user.failed_logins += 1
                if user.failed_logins >= MAX_LOGIN_FAILS:
                    user.failed_logins, user.locked_until = 0, now + timedelta(minutes=LOCK_MINUTES)
                db.session.commit()
            flash("이메일 또는 비밀번호가 올바르지 않습니다.", "error")
    return render_template("login.html", email=email, next=next_url)


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("로그아웃되었습니다.", "info")
    return redirect(url_for("main.home"))


# ---------- 회원가입 ----------

@bp.route("/signup", methods=["GET", "POST"])
def signup():
    if g.user:
        return redirect(url_for("main.home"))
    pending = session.get("signup_email")
    form = {"nickname": ""}

    if request.method == "POST":
        action = request.form.get("action")
        if action == "send":
            email, error = normalize_email(request.form.get("email"))
            if not error and User.query.filter_by(email=email).first():
                error = "이미 가입된 이메일이에요. 로그인하거나 비밀번호 찾기를 이용해주세요."
            error = error or issue_code(email, "signup")
            if error:
                flash(error, "error")
                return render_template("signup.html", step="email", email=request.form.get("email", ""))
            session["signup_email"] = email
            flash(f"{email} 으로 인증번호를 보냈어요. 메일함(스팸함 포함)을 확인해주세요.", "success")
            return redirect(url_for("auth.signup"))

        if action == "restart":
            session.pop("signup_email", None)
            return redirect(url_for("auth.signup"))

        if action == "create" and pending:
            form["nickname"] = request.form.get("nickname", "").strip()
            pw, pw2 = request.form.get("password", ""), request.form.get("password2", "")
            error = (None if request.form.get("agree") else "이용약관과 개인정보처리방침에 동의해주세요.") \
                or validate_nickname(form["nickname"]) or validate_password(pw, pw2) \
                or check_code(pending, "signup", request.form.get("code"))
            if not error and User.query.filter_by(email=pending).first():
                error = "이미 가입된 이메일이에요."
            if not error:
                user = User(nickname=form["nickname"], email=pending, password_hash=generate_password_hash(pw))
                db.session.add(user)
                try:
                    db.session.commit()
                except IntegrityError:
                    db.session.rollback()
                    error = "이미 사용 중인 닉네임 또는 이메일입니다."
                else:
                    login_user(user)
                    flash(f"가입을 환영합니다, {user.nickname}님! 글을 쓰면 {current_app.config['POINTS_POST']}P, "
                          f"댓글을 달면 {current_app.config['POINTS_COMMENT']}P를 받아요.", "success")
                    return redirect(url_for("main.home"))
            db.session.commit()  # 틀린 인증번호 시도 횟수 저장
            flash(error, "error")

    if pending:
        return render_template("signup.html", step="verify", email=pending, form=form)
    return render_template("signup.html", step="email", email="")


# ---------- 비밀번호 찾기 ----------

@bp.route("/forgot", methods=["GET", "POST"])
def forgot():
    pending = session.get("reset_email")
    if request.method == "POST":
        action = request.form.get("action")
        if action == "send":
            email, error = normalize_email(request.form.get("email"))
            # 가입 여부를 알려주지 않기 위해, 없는 계정이어도 같은 안내를 보여준다
            if not error and User.query.filter_by(email=email).first():
                error = issue_code(email, "reset")
            if error:
                flash(error, "error")
                return render_template("forgot.html", step="email", email=request.form.get("email", ""))
            session["reset_email"] = email
            flash("가입된 이메일이라면 인증번호를 보냈어요. 메일함을 확인해주세요.", "success")
            return redirect(url_for("auth.forgot"))
        if action == "restart":
            session.pop("reset_email", None)
            return redirect(url_for("auth.forgot"))
        if action == "reset" and pending:
            pw, pw2 = request.form.get("password", ""), request.form.get("password2", "")
            error = validate_password(pw, pw2) or check_code(pending, "reset", request.form.get("code"))
            user = User.query.filter_by(email=pending).first()
            if not error and user:
                user.password_hash = generate_password_hash(pw)
                user.failed_logins, user.locked_until = 0, None
                db.session.commit()
                session.pop("reset_email", None)
                flash("비밀번호가 변경되었어요. 새 비밀번호로 로그인해주세요.", "success")
                return redirect(url_for("auth.login"))
            db.session.commit()
            flash(error or "인증번호가 만료되었어요. 다시 받아주세요.", "error")
    if pending:
        return render_template("forgot.html", step="verify", email=pending)
    return render_template("forgot.html", step="email", email="")


# ---------- 내 계정 ----------

@bp.route("/me/password", methods=["POST"])
@login_required
def change_password():
    if not check_password_hash(g.user.password_hash or _DUMMY_HASH, request.form.get("current", "")):
        flash("현재 비밀번호가 올바르지 않습니다.", "error")
    else:
        error = validate_password(request.form.get("password", ""), request.form.get("password2", ""))
        if error:
            flash(error, "error")
        else:
            g.user.password_hash = generate_password_hash(request.form["password"])
            db.session.commit()
            flash("비밀번호가 변경되었습니다.", "success")
    return redirect(url_for("main.mypage"))


@bp.route("/me/withdraw", methods=["POST"])
@login_required
def withdraw():
    """회원 탈퇴: 개인정보는 지우고, 글/댓글은 '탈퇴회원'으로 남긴다."""
    if not check_password_hash(g.user.password_hash or _DUMMY_HASH, request.form.get("password", "")):
        flash("비밀번호가 올바르지 않습니다.", "error")
        return redirect(url_for("main.mypage"))
    user = g.user
    PointLog.query.filter_by(user_id=user.id).delete()
    UserConPack.query.filter_by(user_id=user.id).delete()
    user.nickname = f"탈퇴회원{user.id}"
    user.email = None
    user.password_hash = None
    user.points = 0
    user.is_admin = False
    db.session.commit()
    session.clear()
    flash("탈퇴가 완료되었습니다. 그동안 이용해주셔서 감사합니다.", "info")
    return redirect(url_for("main.home"))
