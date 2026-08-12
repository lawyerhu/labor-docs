import logging
import re

import httpx
from fastapi import HTTPException, status

from app.config import get_settings


logger = logging.getLogger(__name__)


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
    api_key = _clean_setting(settings.resend_api_key, "RESEND_API_KEY")
    sender = _clean_setting(settings.registration_email_from, "REGISTRATION_EMAIL_FROM")
    if not api_key or not sender:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "邮件服务尚未配置")

    payload = {
        "from": sender,
        "to": [recipient],
        "subject": "劳动文书助手登录验证码",
        "text": f"你的登录验证码是：{code}\n\n验证码10分钟内有效。若非本人操作，请忽略本邮件。",
        "html": f"<p>你的登录验证码是：</p><p style=\"font-size: 28px; font-weight: 700; letter-spacing: 6px;\">{code}</p><p>验证码10分钟内有效。若非本人操作，请忽略本邮件。</p>",
    }
    try:
        response = httpx.post(
            "https://api.resend.com/emails",
            headers={
                "authorization": f"Bearer {api_key}",
                "content-type": "application/json",
            },
            json=payload,
            timeout=15,
        )
        if response.is_success:
            return
        try:
            error_body = response.json()
        except ValueError:
            error_body = {}
        error_code = str(error_body.get("code") or "")
        error_message = str(error_body.get("message") or "")
        logger.error(
            "[RESEND-SEND-ERROR] status=%s code=%s message=%s",
            response.status_code,
            error_code[:80],
            error_message[:300],
        )
        normalized_error = f"{error_code} {error_message}".lower()
        if response.status_code == 401 or (
            response.status_code == 403 and "domain" not in normalized_error
        ):
            detail = "Resend API 密钥无效、已过期或没有邮件发送权限"
        elif response.status_code == 403 and "domain" in normalized_error:
            detail = "Resend 发件人域名未验证或没有发送权限"
        elif response.status_code == 429:
            detail = "Resend 邮件发送额度或频率已达到限制"
        elif response.status_code in {400, 422} and any(
            word in normalized_error for word in ("sender", "from", "email")
        ):
            detail = "Resend 发件人域名未验证或配置格式不正确"
        else:
            detail = f"Resend 邮件服务返回错误（HTTP {response.status_code}）"
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail)
    except HTTPException:
        raise
    except (httpx.HTTPError, ValueError) as exc:
        logger.exception("[RESEND-SEND-ERROR] request failed")
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "验证码邮件发送失败，请稍后重试") from exc
