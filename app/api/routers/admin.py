from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, require_roles
from app.db.session import get_db
from app.models.user import UserRole
from app.schemas.admin import AdminSummary
from app.schemas.common import ApiResponse
from app.schemas.user import UserAdminUpdate, UserPage, UserResponse
from app.services.admin_service import AdminService
from app.services.user_service import UserService

# 权限挂在路由组上：以后往这个 router 加接口，不会因为忘记加依赖而裸奔
router = APIRouter(dependencies=[Depends(require_roles(UserRole.ADMIN))])


@router.get(
    "/summary",
    response_model=ApiResponse[AdminSummary],
)
async def get_summary(db: AsyncSession = Depends(get_db)) -> ApiResponse[AdminSummary]:
    service = AdminService(db)
    summary = await service.get_summary()

    return ApiResponse[AdminSummary].ok(summary)


@router.get(
    "/users",
    response_model=ApiResponse[UserPage],
)
async def list_users(
    skip: Annotated[int, Query(ge=0, description="跳过的记录数")] = 0,
    limit: Annotated[int, Query(ge=1, le=100, description="每页条数")] = 100,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[UserPage]:
    service = UserService(db)
    items, total = await service.list_users(skip=skip, limit=limit)

    return ApiResponse[UserPage].ok(UserPage(total=total, items=items))


@router.patch(
    "/users/{user_id}",
    response_model=ApiResponse[UserResponse],
)
async def update_user(
    user_id: int,
    payload: UserAdminUpdate,
    current_admin: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[UserResponse]:
    """修改角色或启用状态；管理员不能改自己，避免把系统最后一个管理员锁死。"""
    service = UserService(db)
    user = await service.update_user_admin(user_id, payload, current_admin)

    return ApiResponse[UserResponse].ok(user)
