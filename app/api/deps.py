from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.core.security import BEARER_CHALLENGE_HEADERS, decode_access_token
from app.db.session import get_db
from app.models.user import User, UserRole
from app.services.token_service import TokenService
from app.services.user_service import UserService

# auto_error=False：缺少/格式错误的 Authorization 头不交给 FastAPI 自己处理，
# 否则会返回 {"detail": ...} 的 403/401，绕过项目的统一响应外壳。
bearer_scheme = HTTPBearer(auto_error=False)


async def get_token_payload(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """认证第一层：令牌本身是否有效（签名、有效期、类型）以及是否已被登出撤销。"""
    if credentials is None:
        raise AppException(
            message="Not authenticated",
            code="NOT_AUTHENTICATED",
            status_code=401,
            headers=BEARER_CHALLENGE_HEADERS,
        )

    payload = decode_access_token(credentials.credentials)

    # 登出撤销表：jti 命中说明这枚令牌已被主动作废，不能等它自然过期
    if await TokenService(db).is_revoked(str(payload["jti"])):
        raise AppException(
            message="Token has been revoked",
            code="TOKEN_REVOKED",
            status_code=401,
            headers=BEARER_CHALLENGE_HEADERS,
        )

    return payload


TokenPayload = Annotated[dict[str, Any], Depends(get_token_payload)]


async def get_current_user(
    payload: TokenPayload,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    """认证第二层：令牌有效的前提下，账号必须仍然存在且启用。"""
    try:
        user_id = int(str(payload["sub"]))
    except (TypeError, ValueError) as exc:
        raise AppException(
            message="Invalid authentication token",
            code="INVALID_TOKEN",
            status_code=401,
            headers=BEARER_CHALLENGE_HEADERS,
        ) from exc

    user = await UserService(db).get_user_by_id(user_id)

    # 签名有效但用户已被删除：令牌必须跟着失效，不能因为解出了 id 就放行
    if user is None:
        raise AppException(
            message="User no longer exists",
            code="USER_NOT_FOUND",
            status_code=401,
            headers=BEARER_CHALLENGE_HEADERS,
        )

    if not user.is_active:
        raise AppException(
            message="Account is disabled",
            code="ACCOUNT_DISABLED",
            status_code=403,
        )

    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_roles(*roles: UserRole) -> Callable[..., Awaitable[User]]:
    """角色校验依赖工厂：``Depends(require_roles(UserRole.ADMIN))``。

    能走到这里说明身份已经过了 ``get_current_user``，所以角色不符是 403（无权限），
    而不是 401（未认证）——两者语义不能混用，否则客户端会把「权限不足」当成「需要重新登录」。
    """

    async def _checker(current_user: CurrentUser) -> User:
        if current_user.role not in roles:
            raise AppException(
                message="Insufficient permissions",
                code="FORBIDDEN",
                status_code=403,
            )
        return current_user

    return _checker


AdminUser = Annotated[User, Depends(require_roles(UserRole.ADMIN))]
