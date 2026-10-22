from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, TokenPayload
from app.core.exceptions import AppException
from app.core.ratelimit import login_limiter
from app.core.security import REFRESH_TOKEN_TYPE, decode_token
from app.db.session import get_db
from app.schemas.common import ApiResponse
from app.schemas.user import (
    LogoutRequest,
    RefreshRequest,
    TokenResponse,
    UserCreate,
    UserLogin,
    UserResponse,
)
from app.services.token_service import TokenService
from app.services.user_service import UserService

router = APIRouter()


def _rate_limit_key(request: Request, email: str) -> str:
    """按 IP + 账号计数：只按 IP 会误伤同 NAT 后面的其他人，只按账号则可被随意切换账号绕过。"""
    host = request.client.host if request.client else "unknown"
    return f"{host}:{email.strip().lower()}"


@router.post(
    "/register",
    response_model=ApiResponse[UserResponse],
    status_code=status.HTTP_201_CREATED,
)
async def register(
    payload: UserCreate,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[UserResponse]:
    service = UserService(db)
    user = await service.register(payload)

    return ApiResponse[UserResponse].ok(user)


@router.post(
    "/login",
    response_model=ApiResponse[TokenResponse],
)
async def login(
    payload: UserLogin,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[TokenResponse]:
    key = _rate_limit_key(request, payload.email)

    # 先判在锁定期内，避免「已经太多失败还继续跑 bcrypt」——那本身就是被利用的算力消耗点
    retry_after = await login_limiter.retry_after(key)
    if retry_after:
        raise AppException(
            message="Too many login attempts, please try again later",
            code="TOO_MANY_ATTEMPTS",
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            headers={"Retry-After": str(retry_after)},
        )

    service = UserService(db)

    try:
        user = await service.authenticate(payload.email, payload.password)
    except AppException:
        # 口令错、账号不存在、账号被停用都算一次失败尝试
        await login_limiter.record_failure(key)
        raise

    # 登录成功清零，避免用户被自己之前的几次手误长期拖累
    await login_limiter.reset(key)

    return ApiResponse[TokenResponse].ok(TokenService(db).issue_pair(user))


@router.post(
    "/refresh",
    response_model=ApiResponse[TokenResponse],
)
async def refresh(
    payload: RefreshRequest,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[TokenResponse]:
    """用 refresh 换一对新令牌；旧 refresh 随即作废（轮换）。"""
    return ApiResponse[TokenResponse].ok(await TokenService(db).refresh(payload.refresh_token))


@router.post(
    "/logout",
    response_model=ApiResponse[None],
)
async def logout(
    payload: LogoutRequest,
    token_payload: TokenPayload,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[None]:
    """撤销当前 access 令牌与配套 refresh 令牌。

    两者必须一起撤：只撤 access 的话，客户端拿 refresh 立刻又能换回一对新令牌。
    """
    # 允许已过期的 refresh 也能被登记：verify_exp=False 时仍会校验签名与 iss/aud
    refresh_payload = decode_token(
        payload.refresh_token,
        expected_type=REFRESH_TOKEN_TYPE,
        verify_exp=False,
    )

    # 防止拿别人的 refresh 来「登出」别人
    if str(refresh_payload["sub"]) != str(current_user.id):
        raise AppException(
            message="Refresh token does not belong to current user",
            code="INVALID_REFRESH_TOKEN",
            status_code=400,
        )

    await TokenService(db).revoke(
        (str(token_payload["jti"]), int(token_payload["exp"])),
        (str(refresh_payload["jti"]), int(refresh_payload["exp"])),
    )

    return ApiResponse[None].ok(message="logged out")


@router.get(
    "/me",
    response_model=ApiResponse[UserResponse],
)
async def read_current_user(current_user: CurrentUser) -> ApiResponse[UserResponse]:
    """需要 ``Authorization: Bearer <access_token>``；无/过期/伪造/已撤销令牌均返回 401。"""
    return ApiResponse[UserResponse].ok(current_user)
