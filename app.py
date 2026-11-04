import os

from flask import Flask, g, render_template, session

import auth
import board
import cons
import main
from config import Config
from models import User, db
from utils import check_csrf, register_template_helpers


def create_app(config_class=Config):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(config_class)
    os.makedirs(app.instance_path, exist_ok=True)
    os.makedirs(os.path.join(app.config["UPLOAD_DIR"], "cons"), exist_ok=True)

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

    @app.errorhandler(400)
    @app.errorhandler(403)
    @app.errorhandler(404)
    @app.errorhandler(413)
    def error_page(e):
        messages = {
            403: "권한이 없습니다.",
            404: "페이지를 찾을 수 없습니다.",
            413: "업로드한 파일이 너무 큽니다.",
        }
        msg = messages.get(e.code) or getattr(e, "description", "잘못된 요청입니다.")
        return render_template("error.html", code=e.code, message=msg), e.code

    with app.app_context():
        db.create_all()
    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=True)
