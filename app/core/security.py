from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import bcrypt
import jwt

from app.core.config import settings
from app.core.exceptions import AppException

# bcrypt 的输入上限是 72 字节（不是字符）：超长必须提前拒绝，
# 否则 bcrypt 5.x 会直接抛 ValueError 变成 500，旧版本则会静默截断。
BCRYPT_MAX_PASSWORD_BYTES = 72

# RFC 6750：401 响应必须带 WWW-Authenticate 质询头
BEARER_CHALLENGE_HEADERS = {"WWW-Authenticate": "Bearer"}

ACCESS_TOKEN_TYPE = "access"
REFRESH_TOKEN_TYPE = "refresh"


@dataclass(frozen=True)
class IssuedToken:
    """签发结果：除了给客户端的串，还要带上 jti/过期时间供撤销表落库。"""

    token: str
    jti: str
    expires_at: datetime  # 带时区的 UTC 时间
    expires_in: int  # 剩余秒数，直接给客户端


def hash_password(password: str) -> str:
    """返回可直接入库的 bcrypt 哈希串（含随机 salt）。"""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed_password: str) -> bool:
    """校验明文口令；哈希串损坏时按校验失败处理，而不是抛 500。"""
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed_password.encode("utf-8"))
    except ValueError:
        return False


def _create_token(subject: str, token_type: str, expires_delta: timedelta) -> IssuedToken:
    now = datetime.now(UTC)
    expire = now + expires_delta
    # jti 是撤销的抓手：没有它，登出后已签发的令牌只能等自然过期
    jti = uuid4().hex

    payload = {
        "sub": subject,
        "iat": now,
        "exp": expire,
        "iss": settings.JWT_ISSUER,
        "aud": settings.JWT_AUDIENCE,
        "type": token_type,
        "jti": jti,
    }

    token = jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)

    return IssuedToken(
        token=token,
        jti=jti,
        expires_at=expire,
        expires_in=int(expires_delta.total_seconds()),
    )


def create_access_token(subject: str, expires_delta: timedelta | None = None) -> IssuedToken:
    """签发访问令牌。``sub`` 用字符串：JWT 规范要求 StringOrURI。"""
    expires_delta = expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    return _create_token(subject, ACCESS_TOKEN_TYPE, expires_delta)


def create_refresh_token(subject: str, expires_delta: timedelta | None = None) -> IssuedToken:
    """签发刷新令牌：只用来换新的一对令牌，不能直接访问业务接口。"""
    expires_delta = expires_delta or timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    return _create_token(subject, REFRESH_TOKEN_TYPE, expires_delta)


def decode_token(token: str, *, expected_type: str, verify_exp: bool = True) -> dict[str, Any]:
    """解析并校验令牌；签名/iss/aud/claims 不合格统一翻译成 401。

    ``verify_exp=False`` 只用于登出撤销：令牌已过期也要能把它记进撤销表（虽然此时它本来就无效）。
    """
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
            options={
                "require": ["exp", "iat", "sub", "iss", "aud", "jti"],
                "verify_exp": verify_exp,
            },
        )
    except jwt.ExpiredSignatureError as exc:
        raise AppException(
            message="Token has expired",
            code="TOKEN_EXPIRED",
            status_code=401,
            headers=BEARER_CHALLENGE_HEADERS,
        ) from exc
    except jwt.InvalidTokenError as exc:
        # 签名不对、格式错误、缺字段都走这里，对外统一不说具体原因
        raise AppException(
            message="Invalid authentication token",
            code="INVALID_TOKEN",
            status_code=401,
            headers=BEARER_CHALLENGE_HEADERS,
        ) from exc

    # 类型必须严格匹配：refresh 不能当 access 用，反之亦然
    if payload.get("type") != expected_type:
        raise AppException(
            message="Invalid authentication token",
            code="INVALID_TOKEN",
            status_code=401,
            headers=BEARER_CHALLENGE_HEADERS,
        )

    return payload


def decode_access_token(token: str) -> dict[str, Any]:
    return decode_token(token, expected_type=ACCESS_TOKEN_TYPE)
