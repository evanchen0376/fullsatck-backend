from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
import jwt

from app.core.config import settings
from app.core.exceptions import AppException

# bcrypt 的输入上限是 72 字节（不是字符）：超长必须提前拒绝，
# 否则 bcrypt 5.x 会直接抛 ValueError 变成 500，旧版本则会静默截断。
BCRYPT_MAX_PASSWORD_BYTES = 72

# RFC 6750：401 响应必须带 WWW-Authenticate 质询头
BEARER_CHALLENGE_HEADERS = {"WWW-Authenticate": "Bearer"}


def hash_password(password: str) -> str:
    """返回可直接入库的 bcrypt 哈希串（含随机 salt）。"""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed_password: str) -> bool:
    """校验明文口令；哈希串损坏时按校验失败处理，而不是抛 500。"""
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed_password.encode("utf-8"))
    except ValueError:
        return False


def create_access_token(subject: str, expires_delta: timedelta | None = None) -> tuple[str, int]:
    """签发访问令牌，返回 (token, 有效期秒数)。

    ``sub`` 必须是字符串：JWT 规范要求 sub 为 StringOrURI，用 int 会让部分客户端解析失败。
    """
    now = datetime.now(UTC)
    expires_delta = expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    expire = now + expires_delta

    payload = {
        "sub": subject,
        "iat": now,
        "exp": expire,
        "iss": settings.JWT_ISSUER,
        "aud": settings.JWT_AUDIENCE,
        # 显式声明类型：将来若加 refresh token，靠这个字段区分，避免两种令牌互相冒用
        "type": "access",
    }

    token = jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)

    return token, int(expires_delta.total_seconds())


def decode_access_token(token: str) -> dict[str, Any]:
    """解析并校验令牌；过期、签名错误、类型不符统一翻译成 401。"""
    try:
        payload: dict[str, Any] = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            # issuer/audience 由 PyJWT 直接校验：iss 不匹配抛 InvalidIssuerError，
            # aud 不匹配抛 InvalidAudienceError，缺字段抛 MissingRequiredClaimError，
            # 三者都是 InvalidTokenError 的子类，统一落到下面的 401 分支
            issuer=settings.JWT_ISSUER,
            audience=settings.JWT_AUDIENCE,
            options={"require": ["exp", "iat", "sub", "iss", "aud"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise AppException(
            message="Token has expired",
            code="TOKEN_EXPIRED",
            status_code=401,
            headers=BEARER_CHALLENGE_HEADERS,
        ) from exc
    except jwt.InvalidTokenError as exc:
        # 签名不对、格式错误、缺 exp/sub 都走这里，对外统一不说具体原因
        raise AppException(
            message="Invalid authentication token",
            code="INVALID_TOKEN",
            status_code=401,
            headers=BEARER_CHALLENGE_HEADERS,
        ) from exc

    if payload.get("type") != "access":
        raise AppException(
            message="Invalid authentication token",
            code="INVALID_TOKEN",
            status_code=401,
            headers=BEARER_CHALLENGE_HEADERS,
        )

    return payload
