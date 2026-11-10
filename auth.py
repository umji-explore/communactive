"""회원가입 (식별 코드 + 비밀번호 + 보안 코드) · 로그인 · 비밀번호 찾기(복구 코드) · 탈퇴"""
import re
import secrets
import time
from datetime import timedelta

from flask import Blueprint, flash, g, redirect, render_template, request, session, url_for
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash

import captcha
from models import PointLog, User, UserConPack, db, utcnow
from utils import NICKNAME_RE, login_required, safe_next

bp = Blueprint("auth", __name__)

RESERVED_NICK_PREFIXES = ("탈퇴회원", "관리자", "운영자")
RESERVED_USERNAMES = {"admin", "administrator", "root", "communactive", "manager", "system", "operator"}
USERNAME_RE = re.compile(r"^[a-z][a-z0-9_]{3,19}$")
MAX_LOGIN_FAILS = 5
LOCK_MINUTES = 10
ADMIN_LOGIN_FAILS = 3
ADMIN_LOCK_MINUTES = 30
SIGNUPS_PER_IP_PER_HOUR = 3
RECOVERY_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_DUMMY_HASH = generate_password_hash("dummy-password-for-timing")


# ---------- 검사 ----------

def validate_username(username):
    if not USERNAME_RE.match(username):
        return "식별 코드는 영문 소문자로 시작하는 4~20자 (영문 소문자, 숫자, _) 로 정해주세요."
    if username in RESERVED_USERNAMES:
        return "사용할 수 없는 식별 코드입니다."
    if User.query.filter_by(username=username).first():
        return "이미 사용 중인 식별 코드입니다."
    return None


def validate_password(pw, pw2, username=""):
    if pw != pw2:
        return "비밀번호 확인이 일치하지 않습니다."
    if not 8 <= len(pw) <= 64:
        return "비밀번호는 8~64자로 입력해주세요."
    if not (re.search(r"[A-Za-z]", pw) and re.search(r"\d", pw)):
        return "비밀번호에 영문과 숫자를 모두 넣어주세요."
    if username and username in pw.lower():
        return "비밀번호에 식별 코드를 넣을 수 없습니다."
    return None


def validate_nickname(nickname):
    if not NICKNAME_RE.match(nickname):
        return "닉네임은 2~12자의 한글, 영문, 숫자, _ 만 쓸 수 있습니다."
    if nickname.startswith(RESERVED_NICK_PREFIXES) or nickname.lower() in {"admin", "communactive"}:
        return "사용할 수 없는 닉네임입니다."
    if User.query.filter(db.func.lower(User.nickname) == nickname.lower()).first():
        return "이미 사용 중인 닉네임입니다."
    return None


def new_recovery_code():
    raw = "".join(secrets.choice(RECOVERY_CHARS) for _ in range(16))
    return "-".join(raw[i:i + 4] for i in range(0, 16, 4))  # ABCD-EFGH-JKLM-NPQR


def normalize_recovery(code):
    return re.sub(r"[^A-Z0-9]", "", (code or "").upper())


def login_user(user):
    session.clear()
    session["uid"] = user.id
    session["login_at"] = time.time()
    session.permanent = not user.is_admin  # 사이트 관리자는 브라우저를 닫으면 로그아웃


# ---------- 로그인 ----------

@bp.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("main.home"))
    next_url = safe_next(request.values.get("next"))
    username = ""
    if request.method == "POST":
        username = (request.form.get("username") or "").strip().lower()
        password = request.form.get("password", "")
        user = User.query.filter_by(username=username).first() if username else None
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
                # 사이트 관리자 계정은 더 엄격하게: 3번 틀리면 30분 잠금
                limit, lock = (ADMIN_LOGIN_FAILS, ADMIN_LOCK_MINUTES) if user.is_admin else (MAX_LOGIN_FAILS, LOCK_MINUTES)
                if user.failed_logins >= limit:
                    user.failed_logins, user.locked_until = 0, now + timedelta(minutes=lock)
                db.session.commit()
            flash("식별 코드 또는 비밀번호가 올바르지 않습니다.", "error")
    return render_template("login.html", username=username, next=next_url)


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
    form = {"username": "", "nickname": ""}
    if request.method == "POST":
        form = {k: (request.form.get(k) or "").strip() for k in form}
        form["username"] = form["username"].lower()
        pw, pw2 = request.form.get("password", ""), request.form.get("password2", "")
        ip = request.remote_addr or ""
        recent = User.query.filter(User.signup_ip == ip, User.created_at >= utcnow() - timedelta(hours=1)).count()

        # 보안 코드는 맨 먼저 확인 (틀리면 다른 정보는 확인하지 않음 → 아이디 존재 여부 탐색 방지)
        error = None if captcha.check(request.form.get("captcha")) else "보안 코드가 올바르지 않아요. 그림을 보고 다시 입력해주세요."
        error = error or (None if request.form.get("agree") else "이용약관과 개인정보처리방침에 동의해주세요.") \
            or validate_username(form["username"]) or validate_password(pw, pw2, form["username"]) \
            or validate_nickname(form["nickname"]) \
            or ("같은 곳에서 너무 많이 가입했어요. 잠시 후 다시 시도해주세요." if recent >= SIGNUPS_PER_IP_PER_HOUR else None)
        if not error:
            code = new_recovery_code()
            user = User(username=form["username"], nickname=form["nickname"], signup_ip=ip,
                        password_hash=generate_password_hash(pw),
                        recovery_hash=generate_password_hash(normalize_recovery(code)))
            db.session.add(user)
            try:
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                error = "이미 사용 중인 식별 코드 또는 닉네임입니다."
            else:
                login_user(user)
                session["show_recovery"] = code  # 가입 완료 화면에서 한 번만 보여줌
                return redirect(url_for("auth.welcome"))
        flash(error, "error")
    return render_template("signup.html", form=form)


@bp.route("/welcome")
@login_required
def welcome():
    code = session.pop("show_recovery", None)
    if not code:
        return redirect(url_for("main.home"))
    return render_template("welcome.html", code=code)


# ---------- 비밀번호 찾기 (복구 코드) ----------

@bp.route("/forgot", methods=["GET", "POST"])
def forgot():
    username = ""
    if request.method == "POST":
        username = (request.form.get("username") or "").strip().lower()
        pw, pw2 = request.form.get("password", ""), request.form.get("password2", "")
        user = User.query.filter_by(username=username).first() if username else None
        error = None if captcha.check(request.form.get("captcha")) else "보안 코드가 올바르지 않아요."
        if not error:
            ok = user and user.recovery_hash and check_password_hash(user.recovery_hash, normalize_recovery(request.form.get("recovery")))
            if not user:
                check_password_hash(_DUMMY_HASH, "x")
            error = None if ok else "식별 코드 또는 복구 코드가 올바르지 않습니다."
        error = error or validate_password(pw, pw2, username)
        if not error:
            code = new_recovery_code()  # 쓴 복구 코드는 폐기하고 새로 발급
            user.password_hash = generate_password_hash(pw)
            user.recovery_hash = generate_password_hash(normalize_recovery(code))
            user.failed_logins, user.locked_until = 0, None
            db.session.commit()
            login_user(user)
            session["show_recovery"] = code
            flash("비밀번호가 변경되었어요. 복구 코드가 새로 발급되었으니 꼭 다시 저장해주세요.", "success")
            return redirect(url_for("auth.welcome"))
        flash(error, "error")
    return render_template("forgot.html", username=username)


# ---------- 내 계정 ----------

@bp.route("/me/password", methods=["POST"])
@login_required
def change_password():
    if not check_password_hash(g.user.password_hash or _DUMMY_HASH, request.form.get("current", "")):
        flash("현재 비밀번호가 올바르지 않습니다.", "error")
    else:
        error = validate_password(request.form.get("password", ""), request.form.get("password2", ""), g.user.username)
        if error:
            flash(error, "error")
        else:
            g.user.password_hash = generate_password_hash(request.form["password"])
            db.session.commit()
            flash("비밀번호가 변경되었습니다.", "success")
    return redirect(url_for("main.mypage"))


@bp.route("/me/recovery", methods=["POST"])
@login_required
def regenerate_recovery():
    """복구 코드를 잃어버렸을 때 새로 발급 (비밀번호 확인 필요)"""
    if not check_password_hash(g.user.password_hash or _DUMMY_HASH, request.form.get("password", "")):
        flash("비밀번호가 올바르지 않습니다.", "error")
        return redirect(url_for("main.mypage"))
    code = new_recovery_code()
    g.user.recovery_hash = generate_password_hash(normalize_recovery(code))
    db.session.commit()
    session["show_recovery"] = code
    return redirect(url_for("auth.welcome"))


@bp.route("/me/withdraw", methods=["POST"])
@login_required
def withdraw():
    """회원 탈퇴: 계정 정보는 지우고, 글/댓글은 '탈퇴회원'으로 남긴다."""
    if not check_password_hash(g.user.password_hash or _DUMMY_HASH, request.form.get("password", "")):
        flash("비밀번호가 올바르지 않습니다.", "error")
        return redirect(url_for("main.mypage"))
    user = g.user
    PointLog.query.filter_by(user_id=user.id).delete()
    UserConPack.query.filter_by(user_id=user.id).delete()
    user.nickname = f"탈퇴회원{user.id}"
    user.username = None
    user.password_hash = None
    user.recovery_hash = None
    user.signup_ip = ""
    user.points = 0
    user.is_admin = False
    db.session.commit()
    session.clear()
    flash("탈퇴가 완료되었습니다. 그동안 이용해주셔서 감사합니다.", "info")
    return redirect(url_for("main.home"))
