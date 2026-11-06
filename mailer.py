"""인증번호 메일 발송 (SMTP)"""
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr

from flask import current_app


class MailNotConfigured(Exception):
    pass


def send_mail(to, subject, body):
    cfg = current_app.config
    if not cfg["SMTP_HOST"]:
        if cfg["MAIL_DEBUG_ECHO"]:
            current_app.logger.warning("[메일 미설정] %s 에게 보낼 메일:\n%s", to, body)
            return
        raise MailNotConfigured()

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr(("communactive", cfg["MAIL_FROM"]))
    msg["To"] = to
    msg.set_content(body)

    context = ssl.create_default_context()
    if cfg["SMTP_PORT"] == 465:
        with smtplib.SMTP_SSL(cfg["SMTP_HOST"], 465, context=context, timeout=15) as s:
            s.login(cfg["SMTP_USER"], cfg["SMTP_PASSWORD"])
            s.send_message(msg)
    else:
        with smtplib.SMTP(cfg["SMTP_HOST"], cfg["SMTP_PORT"], timeout=15) as s:
            s.starttls(context=context)
            s.login(cfg["SMTP_USER"], cfg["SMTP_PASSWORD"])
            s.send_message(msg)


def send_code(to, code, purpose):
    what = "회원가입" if purpose == "signup" else "비밀번호 재설정"
    body = (
        f"communactive {what} 인증번호입니다.\n\n"
        f"    {code}\n\n"
        f"10분 안에 입력해주세요.\n"
        f"본인이 요청하지 않았다면 이 메일을 무시하세요."
    )
    send_mail(to, f"[communactive] {what} 인증번호 {code}", body)
