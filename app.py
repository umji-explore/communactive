import os

import click
from flask import Flask, g, render_template, session
from werkzeug.middleware.proxy_fix import ProxyFix

import auth
import board
import cons
import main
from config import ON_VERCEL, Config
from models import User, db
from utils import check_csrf, register_template_helpers


def create_app(config_class=Config):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(config_class)
    if not ON_VERCEL:  # Vercel 은 파일 시스템이 읽기 전용
        os.makedirs(app.instance_path, exist_ok=True)
        os.makedirs(os.path.join(app.config["UPLOAD_DIR"], "cons"), exist_ok=True)
    else:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    db.init_app(app)
    auth.init_oauth(app)
    register_template_helpers(app)

    for module in (main, auth, board, cons):
        app.register_blueprint(module.bp)

    @app.before_request
    def load_user():
        uid = session.get("uid")
        g.user = db.session.get(User, uid) if uid else None
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
        msg = messages.get(code) if code != 400 else getattr(e, "description", None)
        return render_template("error.html", code=code, message=msg or "잘못된 요청입니다."), code

    @app.cli.command("make-admin")
    @click.argument("nickname")
    def make_admin(nickname):
        """flask --app app make-admin <닉네임> : 관리자 지정 (모든 글/댓글 삭제 가능)"""
        user = User.query.filter_by(nickname=nickname).first()
        if not user:
            raise click.ClickException(f"'{nickname}' 회원이 없습니다.")
        user.is_admin = True
        db.session.commit()
        click.echo(f"{nickname} 님을 관리자로 지정했습니다.")

    with app.app_context():
        db.create_all()
    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=True)
