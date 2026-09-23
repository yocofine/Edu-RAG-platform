from datetime import datetime, timedelta, timezone
from secrets import token_urlsafe

import jwt
import bcrypt

from .config import get_settings


settings = get_settings()


def hash_password(password: str) -> str:
    encoded = password.encode("utf-8")
    if len(encoded) > 72:
        raise ValueError("密码UTF-8编码后不能超过72字节")
    return bcrypt.hashpw(encoded, bcrypt.gensalt(rounds=12)).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))
    except (ValueError, UnicodeError):
        return False


def create_token(user_id: int, role: str, token_type: str, lifetime: timedelta) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {"sub": str(user_id), "role": role, "type": token_type, "iat": now, "exp": now + lifetime},
        settings.jwt_secret,
        algorithm="HS256",
    )


def create_access_token(user_id: int, role: str) -> str:
    return create_token(user_id, role, "access", timedelta(minutes=settings.access_token_minutes))


def create_refresh_token(user_id: int, role: str) -> str:
    return create_token(user_id, role, "refresh", timedelta(days=settings.refresh_token_days))


def decode_token(token: str, expected_type: str) -> dict:
    payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    if payload.get("type") != expected_type:
        raise jwt.InvalidTokenError("token type mismatch")
    return payload


def csrf_token() -> str:
    return token_urlsafe(32)
