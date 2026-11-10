"""AI 이미지 만들기 (Vercel AI Gateway)

- 포인트를 먼저 차감하고, 실패하면 돌려준다.
- 비용 관리를 위해 1인당 하루 횟수와 사이트 전체 하루 횟수를 제한한다.
- 인증: AI_GATEWAY_API_KEY 가 있으면 그것을, 없으면 Vercel 배포 환경의 OIDC 토큰을 쓴다.
"""
import base64
import os
from datetime import timedelta

import requests
from flask import Blueprint, current_app, g, jsonify, request

from models import Upload, db, utcnow
from uploads import UploadError, process, store
from utils import KST, add_points, con_url, spend_points

bp = Blueprint("ai", __name__)
GATEWAY_URL = "https://ai-gateway.vercel.sh/v1/images/generations"


class AIError(Exception):
    pass


def _gateway_token():
    key = os.environ.get("AI_GATEWAY_API_KEY")
    if key:
        return key
    tok = request.headers.get("x-vercel-oidc-token") or os.environ.get("VERCEL_OIDC_TOKEN")
    if tok:
        return tok
    try:
        from vercel.oidc import get_vercel_oidc_token_sync
        return get_vercel_oidc_token_sync()
    except Exception:
        return None


def _today_start_utc():
    now_kst = utcnow() + KST
    return now_kst.replace(hour=0, minute=0, second=0, microsecond=0) - KST


def generate_image(prompt):
    token = _gateway_token()
    if not token:
        raise AIError("AI 이미지 기능이 아직 설정되지 않았어요.")
    try:
        r = requests.post(GATEWAY_URL, timeout=120, headers={"Authorization": f"Bearer {token}"},
                          json={"model": current_app.config["AI_IMAGE_MODEL"], "prompt": prompt,
                                "n": 1, "response_format": "b64_json"})
    except requests.RequestException:
        raise AIError("AI 서버에 연결하지 못했어요. 잠시 후 다시 시도해주세요.")
    if r.status_code != 200:
        current_app.logger.warning("AI 이미지 실패 %s: %s", r.status_code, r.text[:300])
        if r.status_code in (400, 422):
            raise AIError("이 내용으로는 이미지를 만들 수 없어요. 다른 설명을 써보세요.")
        if r.status_code == 429:
            raise AIError("요청이 많아요. 잠시 후 다시 시도해주세요.")
        raise AIError("이미지를 만들지 못했어요. 잠시 후 다시 시도해주세요.")
    item = (r.json().get("data") or [{}])[0]
    if item.get("b64_json"):
        return base64.b64decode(item["b64_json"])
    if item.get("url"):
        img = requests.get(item["url"], timeout=30)
        if img.ok:
            return img.content
    raise AIError("이미지를 받지 못했어요.")


@bp.route("/ai-image", methods=["POST"])
def ai_image():
    cfg = current_app.config
    if g.user is None:
        return jsonify(error="로그인이 필요합니다."), 401
    prompt = (request.form.get("prompt") or "").strip()
    if not 2 <= len(prompt) <= 300:
        return jsonify(error="어떤 그림을 원하는지 2~300자로 적어주세요."), 400
    start = _today_start_utc()
    mine = Upload.query.filter(Upload.user_id == g.user.id, Upload.kind == "ai", Upload.created_at >= start).count()
    if mine >= cfg["AI_IMAGE_DAILY"]:
        return jsonify(error=f"AI 이미지는 하루에 {cfg['AI_IMAGE_DAILY']}장까지 만들 수 있어요."), 429
    total = Upload.query.filter(Upload.kind == "ai", Upload.created_at >= start).count()
    if total >= cfg["AI_IMAGE_SITE_DAILY"]:
        return jsonify(error="오늘 AI 이미지 사용량이 모두 찼어요. 내일 다시 시도해주세요."), 429

    cost = cfg["AI_IMAGE_COST"]
    if cost and not spend_points(g.user, cost, "ai_image", f"AI 이미지: {prompt[:40]}"):
        db.session.rollback()
        return jsonify(error=f"포인트가 부족해요. (필요 {cost:,}P)"), 400
    db.session.commit()

    try:
        raw = generate_image(prompt)
        out, ext, w, h = process(raw, "ai")
        up = Upload(user_id=g.user.id, kind="ai", path=store(out, ext, "ai"), width=w, height=h)
        db.session.add(up)
        db.session.commit()
    except (AIError, UploadError) as e:
        db.session.rollback()
        if cost:
            add_points(g.user, cost, "refund", "AI 이미지 실패 환불")
            db.session.commit()
        return jsonify(error=str(e)), 502
    return jsonify(id=up.id, url=con_url(up.path), width=w, height=h, cost=cost)
