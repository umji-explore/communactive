"""Vercel Blob 클라이언트 업로드 토큰 (브라우저 → Blob 직접 업로드용)

Vercel 함수는 요청 하나에 4.5MB 까지만 받을 수 있어서, 동영상처럼 큰 파일은 브라우저가 Blob 에 바로 올린다.
서버는 "이 경로에, 이 형식/크기까지만 올릴 수 있는" 짧은 수명의 토큰만 만들어 준다.
@vercel/blob 의 generateClientTokenFromReadWriteToken 과 똑같은 형식 (테스트로 결과 일치 확인).
"""
import base64
import hashlib
import hmac
import json
import time


def generate_client_token(read_write_token, pathname, allowed_content_types, maximum_size_in_bytes,
                          valid_seconds=600, valid_until_ms=None):
    parts = read_write_token.split("_")
    store_id = parts[3] if len(parts) > 3 else ""
    if not store_id:
        raise ValueError("BLOB_READ_WRITE_TOKEN 형식이 올바르지 않습니다.")
    payload_obj = {
        "pathname": pathname,
        "allowedContentTypes": list(allowed_content_types),
        "maximumSizeInBytes": int(maximum_size_in_bytes),
        "addRandomSuffix": False,
        "validUntil": int(valid_until_ms if valid_until_ms is not None else (time.time() + valid_seconds) * 1000),
    }
    payload = base64.b64encode(
        json.dumps(payload_obj, separators=(",", ":"), ensure_ascii=False).encode()
    ).decode()
    secured = hmac.new(read_write_token.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"vercel_blob_client_{store_id}_" + base64.b64encode(f"{secured}.{payload}".encode()).decode()


def store_id_from_token(read_write_token):
    parts = (read_write_token or "").split("_")
    return parts[3].lower() if len(parts) > 3 else ""
