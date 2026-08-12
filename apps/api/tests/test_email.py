from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

from app.services import email


def test_send_otp_email_uses_brevo_http_api(monkeypatch):
    settings = SimpleNamespace(
        app_env="production",
        brevo_api_key="xkeysib-test",
        brevo_sender_email="sender@example.com",
        brevo_sender_name="劳动文书助手",
    )
    calls = {}

    class Response:
        is_success = True

    def fake_post(url, **kwargs):
        calls["url"] = url
        calls["kwargs"] = kwargs
        return Response()

    monkeypatch.setattr(email, "get_settings", lambda: settings)
    monkeypatch.setattr(httpx, "post", fake_post)

    email.send_otp_email("recipient@example.com", "123456")

    assert calls["url"] == "https://api.brevo.com/v3/smtp/email"
    assert calls["kwargs"]["headers"]["api-key"] == "xkeysib-test"
    assert calls["kwargs"]["json"]["sender"] == {
        "email": "sender@example.com",
        "name": "劳动文书助手",
    }
    assert calls["kwargs"]["json"]["to"] == [{"email": "recipient@example.com"}]
    assert calls["kwargs"]["json"]["textContent"].startswith("你的登录验证码是：123456")


def test_send_otp_email_requires_brevo_configuration(monkeypatch):
    settings = SimpleNamespace(
        app_env="production",
        brevo_api_key=None,
        brevo_sender_email=None,
        brevo_sender_name="劳动文书助手",
    )
    monkeypatch.setattr(email, "get_settings", lambda: settings)

    with pytest.raises(HTTPException):
        email.send_otp_email("recipient@example.com", "123456")


def test_send_otp_email_accepts_accidentally_pasted_env_lines(monkeypatch):
    settings = SimpleNamespace(
        app_env="production",
        brevo_api_key="BREVO_API_KEY=xkeysib-test",
        brevo_sender_email="BREVO_SENDER_EMAIL=sender@example.com",
        brevo_sender_name="劳动文书助手",
    )
    calls = {}

    class Response:
        is_success = True

    def fake_post(url, **kwargs):
        calls["kwargs"] = kwargs
        return Response()

    monkeypatch.setattr(email, "get_settings", lambda: settings)
    monkeypatch.setattr(httpx, "post", fake_post)

    email.send_otp_email("recipient@example.com", "123456")

    assert calls["kwargs"]["headers"]["api-key"] == "xkeysib-test"
    assert calls["kwargs"]["json"]["sender"]["email"] == "sender@example.com"


@pytest.mark.parametrize(
    ("status_code", "body", "expected"),
    [
        (401, {"message": "Key not found"}, "API 密钥无效"),
        (
            403,
            {
                "code": "permission_denied",
                "message": "Unable to send email. Your SMTP account is not yet activated.",
            },
            "事务邮件账户尚未激活",
        ),
        (400, {"message": "sender not valid"}, "发件人邮箱未验证"),
        (429, {"message": "rate limit"}, "额度或频率"),
    ],
)
def test_send_otp_email_explains_brevo_configuration_errors(monkeypatch, status_code, body, expected):
    settings = SimpleNamespace(
        app_env="production",
        brevo_api_key="xkeysib-test",
        brevo_sender_email="sender@example.com",
        brevo_sender_name="劳动文书助手",
    )

    class Response:
        is_success = False

        def __init__(self):
            self.status_code = status_code

        def json(self):
            return body

    monkeypatch.setattr(email, "get_settings", lambda: settings)
    monkeypatch.setattr(httpx, "post", lambda *_args, **_kwargs: Response())

    with pytest.raises(HTTPException) as exc_info:
        email.send_otp_email("recipient@example.com", "123456")

    assert expected in exc_info.value.detail
