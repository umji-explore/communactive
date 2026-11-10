import os
import time

import click
from flask import Flask, g, render_template, session
from werkzeug.middleware.proxy_fix import ProxyFix

import admin
import auth
import board
import captcha
import cons
import embeds
import main
import ranks
import schema
import uploads
from config import ON_VERCEL, Config
from models import User, db
from utils import check_csrf, register_template_helpers


ADMIN_SESSION_SECONDS = 8 * 3600


def create_app(config_class=Config):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(config_class)
    if not ON_VERCEL:  # Vercel 은 파일 시스템이 읽기 전용
        os.makedirs(app.instance_path, exist_ok=True)
        os.makedirs(os.path.join(app.config["UPLOAD_DIR"], "cons"), exist_ok=True)
    else:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    db.init_app(app)
    register_template_helpers(app)
    app.jinja_env.globals["name_tag"] = ranks.name_tag
    app.jinja_env.filters["public_name"] = ranks.public_name

    for module in (main, auth, board, cons, captcha, uploads, embeds, admin):
        app.register_blueprint(module.bp)

    @app.before_request
    def load_user():
        uid = session.get("uid")
        g.user = db.session.get(User, uid) if uid else None
        # 사이트 관리자 계정은 로그인 유지 시간을 짧게 (8시간). 오래된 세션은 다시 로그인해야 한다.
        if g.user is not None and g.user.is_admin:
            if time.time() - session.get("login_at", 0) > ADMIN_SESSION_SECONDS:
                session.clear()
                g.user = None
        if g.user is None and uid:
            session.pop("uid", None)
        check_csrf()

    @app.context_processor
    def inject_globals():
        return {"POINTS_POST": app.config["POINTS_POST"], "POINTS_COMMENT": app.config["POINTS_COMMENT"]}

    @app.after_request
    def security_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        if ON_VERCEL:
            resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        return resp

    @app.errorhandler(400)
    @app.errorhandler(403)
    @app.errorhandler(404)
    @app.errorhandler(413)
    @app.errorhandler(500)
    def error_page(e):
        code = getattr(e, "code", 500)
        messages = {
            403: "권한이 없습니다.",
            404: "페이지를 찾을 수 없습니다.",
            413: "업로드한 파일이 너무 큽니다.",
            500: "일시적인 오류가 발생했습니다. 잠시 후 다시 시도해주세요.",
        }
        if code == 500:
            db.session.rollback()
        desc = getattr(e, "description", "") or ""
        custom = any("가" <= ch <= "힣" for ch in desc)  # 우리가 직접 넣은 한국어 안내면 그대로 보여준다
        msg = desc if custom else messages.get(code)
        return render_template("error.html", code=code, message=msg or "잘못된 요청입니다."), code

    @app.cli.command("make-admin")
    @click.argument("username")
    def make_admin(username):
        """flask --app app make-admin <식별 코드> : 사이트 관리자 지정 (금색 닉네임 + '관리자' 칭호)"""
        user = User.query.filter_by(username=username.strip().lower()).first()
        if not user:
            raise click.ClickException(f"식별 코드 '{username}' 회원이 없습니다.")
        user.is_admin = True
        db.session.commit()
        click.echo(f"{user.nickname} 님을 사이트 관리자로 지정했습니다.")

    with app.app_context():
        schema.upgrade(db)  # 새 테이블 생성 + 기존 테이블에 빠진 컬럼 추가 (데이터 보존)
    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=True)
