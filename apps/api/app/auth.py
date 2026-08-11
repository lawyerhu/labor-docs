import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Cookie, Depends, HTTPException, Response, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import OTPCode, User


def _hash(value: str) -> str:
    settings = get_settings()
    return hashlib.sha256(f"{settings.session_secret}:{value}".encode()).hexdigest()


def issue_otp(db: Session, email: str) -> str:
    window_start = datetime.now(timezone.utc) - timedelta(minutes=15)
    recent = db.scalar(select(func.count(OTPCode.id)).where(OTPCode.email == email.lower(), OTPCode.created_at >= window_start)) or 0
    if recent >= 5:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "验证码请求过于频繁，请15分钟后重试")
    code = f"{secrets.randbelow(1_000_000):06d}"
    record = OTPCode(
        email=email.lower(),
        code_hash=_hash(code),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
    )
    db.add(record)
    db.commit()
    return code


def verify_otp(db: Session, email: str, code: str) -> User:
    record = db.scalar(
        select(OTPCode)
        .where(OTPCode.email == email.lower(), OTPCode.consumed_at.is_(None))
        .order_by(OTPCode.expires_at.desc())
    )
    now = datetime.now(timezone.utc)
    if not record or record.expires_at.replace(tzinfo=timezone.utc) < now:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "验证码无效或已过期")
    if record.attempts >= 5:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "验证码尝试次数过多，请重新获取")
    record.attempts += 1
    if not secrets.compare_digest(record.code_hash, _hash(code)):
        db.commit()
        if record.attempts >= 5:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "验证码尝试次数过多，请重新获取")
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "验证码无效或已过期")
    record.consumed_at = now
    user = db.scalar(select(User).where(User.email == email.lower()))
    if not user:
        user = User(email=email.lower())
        db.add(user)
    db.commit()
    db.refresh(user)
    return user


def set_session_cookie(response: Response, user: User) -> None:
    settings = get_settings()
    token = URLSafeTimedSerializer(settings.session_secret, salt="labor-docs-session").dumps({"uid": user.id})
    response.set_cookie(
        "labor_session",
        token,
        max_age=settings.session_max_age_seconds,
        httponly=True,
        secure=settings.app_env not in {"development", "test"},
        samesite="lax",
        path="/",
    )


def get_current_user(
    labor_session: str | None = Cookie(default=None),
    db: Session = Depends(get_db),
) -> User:
    if not labor_session:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "请先登录")
    settings = get_settings()
    try:
        payload = URLSafeTimedSerializer(settings.session_secret, salt="labor-docs-session").loads(
            labor_session, max_age=settings.session_max_age_seconds
        )
    except (BadSignature, SignatureExpired):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "登录状态已失效") from None
    user = db.get(User, payload.get("uid"))
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户不存在")
    return user
