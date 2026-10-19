from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser
from app.core.security import create_access_token
from app.db.session import get_db
from app.schemas.common import ApiResponse
from app.schemas.user import TokenResponse, UserCreate, UserLogin, UserResponse
from app.services.user_service import UserService

router = APIRouter()


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
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[TokenResponse]:
    service = UserService(db)
    user = await service.authenticate(payload.email, payload.password)

    token, expires_in = create_access_token(str(user.id))

    return ApiResponse[TokenResponse].ok(
        TokenResponse(access_token=token, expires_in=expires_in),
    )


@router.get(
    "/me",
    response_model=ApiResponse[UserResponse],
)
async def read_current_user(current_user: CurrentUser) -> ApiResponse[UserResponse]:
    """需要 ``Authorization: Bearer <access_token>``；无/过期/伪造令牌均返回 401。"""
    return ApiResponse[UserResponse].ok(current_user)
