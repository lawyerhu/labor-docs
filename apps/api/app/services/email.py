import httpx
from fastapi import HTTPException, status

from app.config import get_settings


def send_otp_email(recipient: str, code: str) -> None:
    settings = get_settings()
    if settings.app_env in {"development", "test"}:
        return
    if not settings.brevo_api_key or not settings.brevo_sender_email:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "邮件服务尚未配置")

    payload = {
        "sender": {
            "email": settings.brevo_sender_email,
            "name": settings.brevo_sender_name,
        },
        "to": [{"email": recipient}],
        "subject": "劳动文书助手登录验证码",
        "textContent": f"你的登录验证码是：{code}\n\n验证码10分钟内有效。若非本人操作，请忽略本邮件。",
    }
    try:
        response = httpx.post(
            "https://api.brevo.com/v3/smtp/email",
            headers={
                "accept": "application/json",
                "api-key": settings.brevo_api_key,
                "content-type": "application/json",
            },
            json=payload,
            timeout=15,
        )
        response.raise_for_status()
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "验证码邮件发送失败，请稍后重试") from exc
