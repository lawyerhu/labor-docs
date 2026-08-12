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
        if response.is_success:
            return
        try:
            error_body = response.json()
        except ValueError:
            error_body = {}
        error_code = str(error_body.get("code") or "")
        error_message = str(error_body.get("message") or "")
        logger.error(
            "[BREVO-SEND-ERROR] status=%s code=%s message=%s",
            response.status_code,
            error_code[:80],
            error_message[:300],
        )
        normalized_error = f"{error_code} {error_message}".lower()
        if response.status_code == 403 and (
            "not yet activated" in normalized_error
            or "permission_denied" in normalized_error
        ):
            detail = "Brevo 事务邮件账户尚未激活，请先联系 Brevo 支持申请开通"
        elif response.status_code in {401, 403}:
            detail = "Brevo API 密钥无效或没有邮件发送权限"
        elif response.status_code == 429:
            detail = "Brevo 邮件发送额度或频率已达到限制"
        elif response.status_code == 400 and any(
            word in normalized_error for word in ("sender", "from", "email")
        ):
            detail = "Brevo 发件人邮箱未验证或配置格式不正确"
        else:
            detail = f"Brevo 邮件服务返回错误（HTTP {response.status_code}）"
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail)
    except HTTPException:
        raise
    except (httpx.HTTPError, ValueError) as exc:
        logger.exception("[BREVO-SEND-ERROR] request failed")
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "验证码邮件发送失败，请稍后重试") from exc
