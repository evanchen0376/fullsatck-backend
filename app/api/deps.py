from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.core.security import BEARER_CHALLENGE_HEADERS, decode_access_token
from app.db.session import get_db
from app.models.user import User
from app.services.user_service import UserService

# auto_error=False：缺少/格式错误的 Authorization 头不交给 FastAPI 自己处理，
# 否则会返回 {"detail": ...} 的 403/401，绕过项目的统一响应外壳。
bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    """解析 Bearer 令牌并加载用户；任何一步失败都返回 401/403，绝不返回匿名用户。"""
    if credentials is None:
        raise AppException(
            message="Not authenticated",
            code="NOT_AUTHENTICATED",
            status_code=401,
            headers=BEARER_CHALLENGE_HEADERS,
        )

    payload = decode_access_token(credentials.credentials)

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
