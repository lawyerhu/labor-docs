import re

import httpx
from fastapi import HTTPException, status

from app.config import get_settings


def _clean_setting(value: str | None, key: str) -> str | None:
    """Accept a plain value and tolerate an accidentally pasted KEY=value line."""
    if not value:
        return None
    cleaned = value.strip().strip('"').strip("'")
    return re.sub(rf"^{re.escape(key)}\s*=\s*", "", cleaned, flags=re.IGNORECASE).strip() or None


def send_otp_email(recipient: str, code: str) -> None:
    settings = get_settings()
    if settings.app_env in {"development", "test"}:
        return
    api_key = _clean_setting(settings.brevo_api_key, "BREVO_API_KEY")
    sender_email = _clean_setting(settings.brevo_sender_email, "BREVO_SENDER_EMAIL")
    if not api_key or not sender_email:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "邮件服务尚未配置")

    payload = {
        "sender": {
            "email": sender_email,
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
                "api-key": api_key,
                "content-type": "application/json",
            },
            json=payload,
            timeout=15,
        )
        response.raise_for_status()
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "验证码邮件发送失败，请稍后重试") from exc
