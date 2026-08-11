import smtplib
from email.message import EmailMessage

from fastapi import HTTPException, status

from app.config import get_settings


def send_otp_email(recipient: str, code: str) -> None:
    settings = get_settings()
    if settings.app_env in {"development", "test"}:
        return
    if not settings.smtp_host or not settings.smtp_from:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "邮件服务尚未配置")
    message = EmailMessage()
    message["Subject"] = "劳动文书助手登录验证码"
    message["From"] = settings.smtp_from
    message["To"] = recipient
    message.set_content(f"你的登录验证码是：{code}\n\n验证码10分钟内有效。若非本人操作，请忽略本邮件。")
    smtp_class = smtplib.SMTP_SSL if settings.smtp_use_ssl else smtplib.SMTP
    try:
        with smtp_class(settings.smtp_host, settings.smtp_port, timeout=15) as client:
            if not settings.smtp_use_ssl:
                client.starttls()
            if settings.smtp_username and settings.smtp_password:
                client.login(settings.smtp_username, settings.smtp_password)
            client.send_message(message)
    except Exception as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "验证码邮件发送失败，请稍后重试") from exc
