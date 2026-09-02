from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

from app.services import email


def _settings(**overrides):
    values = {
        "app_env": "production",
        "resend_api_key": "re_test",
        "registration_email_from": "劳动文书助手 <no-reply@example.com>",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_send_otp_email_uses_resend_http_api(monkeypatch):
    calls = {}

    class Response:
        is_success = True

    def fake_post(url, **kwargs):
        calls["url"] = url
        calls["kwargs"] = kwargs
        return Response()

    monkeypatch.setattr(email, "get_settings", lambda: _settings())
    monkeypatch.setattr(httpx, "post", fake_post)

    email.send_otp_email("recipient@example.com", "123456")

    assert calls["url"] == "https://api.resend.com/emails"
    assert calls["kwargs"]["headers"]["authorization"] == "Bearer re_test"
    assert calls["kwargs"]["json"]["from"] == "劳动文书助手 <no-reply@example.com>"
    assert calls["kwargs"]["json"]["to"] == ["recipient@example.com"]
    assert "123456" in calls["kwargs"]["json"]["text"]


def test_send_otp_email_requires_resend_configuration(monkeypatch):
    monkeypatch.setattr(email, "get_settings", lambda: _settings(resend_api_key=None, registration_email_from=None))

    with pytest.raises(HTTPException):
        email.send_otp_email("recipient@example.com", "123456")


def test_send_otp_email_accepts_accidentally_pasted_env_lines(monkeypatch):
    calls = {}

    class Response:
        is_success = True

    def fake_post(_url, **kwargs):
        calls["kwargs"] = kwargs
        return Response()

    monkeypatch.setattr(
        email,
        "get_settings",
        lambda: _settings(
            resend_api_key="RESEND_API_KEY=re_test",
            registration_email_from="REGISTRATION_EMAIL_FROM=sender@example.com",
        ),
    )
    monkeypatch.setattr(httpx, "post", fake_post)

    email.send_otp_email("recipient@example.com", "123456")

    assert calls["kwargs"]["headers"]["authorization"] == "Bearer re_test"
    assert calls["kwargs"]["json"]["from"] == "sender@example.com"


@pytest.mark.parametrize(
    ("status_code", "body", "expected"),
    [
        (401, {"name": "invalid_api_key", "message": "API key is invalid"}, "name=invalid_api_key; message=API key is invalid"),
        (403, {"name": "domain_not_verified", "message": "domain is not verified"}, "name=domain_not_verified; message=domain is not verified"),
        (422, {"name": "validation_error", "message": "sender domain is not verified"}, "name=validation_error; message=sender domain is not verified"),
        (429, {"name": "rate_limit_exceeded", "message": "rate limit"}, "name=rate_limit_exceeded; message=rate limit"),
    ],
)
def test_send_otp_email_explains_resend_errors(monkeypatch, status_code, body, expected):
    class Response:
        is_success = False

        def __init__(self):
            self.status_code = status_code

        def json(self):
            return body

    monkeypatch.setattr(email, "get_settings", lambda: _settings())
    monkeypatch.setattr(httpx, "post", lambda *_args, **_kwargs: Response())

    with pytest.raises(HTTPException) as exc_info:
        email.send_otp_email("recipient@example.com", "123456")

    assert expected in exc_info.value.detail
