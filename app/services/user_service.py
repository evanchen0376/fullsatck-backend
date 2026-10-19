from functools import lru_cache

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.core.security import (
    BCRYPT_MAX_PASSWORD_BYTES,
    BEARER_CHALLENGE_HEADERS,
    hash_password,
    verify_password,
)
from app.models.user import User
from app.schemas.user import UserCreate


@lru_cache(maxsize=1)
def _dummy_password_hash() -> str:
    """账号不存在时也跑一次 bcrypt，抹平响应时间差异，避免被用来枚举用户。

    用固定常量而不是每次现算，是为了让这一步只付一次哈希代价。
    """
    return hash_password("dummy-password-for-timing")


def _invalid_credentials() -> AppException:
    """用户名不存在和口令错误共用同一个响应，不泄露账号是否存在。"""
    return AppException(
        message="Incorrect username or password",
        code="INVALID_CREDENTIALS",
        status_code=401,
        headers=BEARER_CHALLENGE_HEADERS,
    )


class UserService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def register(self, payload: UserCreate) -> User:
        # 用户名保留大小写，邮箱统一小写，避免同一邮箱注册出多个账号
        username = payload.username.strip()
        email = payload.email.strip().lower()

        if len(payload.password.encode("utf-8")) > BCRYPT_MAX_PASSWORD_BYTES:
            raise AppException(
                message=f"Password must be at most {BCRYPT_MAX_PASSWORD_BYTES} bytes",
                code="PASSWORD_TOO_LONG",
                status_code=422,
            )

        # 先查再插只为给出明确的错误码；并发下两个请求仍可能同时通过，
        # 真正的唯一性由 users.username / users.email 的唯一索引保证（见下方 IntegrityError）。
        if await self._exists(username=username):
            raise AppException(
                message="Username already exists",
                code="USERNAME_TAKEN",
                status_code=409,
            )

        if await self._exists(email=email):
            raise AppException(
                message="Email already exists",
                code="EMAIL_TAKEN",
                status_code=409,
            )

        user = User(
            username=username,
            email=email,
            hashed_password=hash_password(payload.password),
        )
        self.db.add(user)

        try:
            await self.db.commit()
        except IntegrityError as exc:
            await self.db.rollback()
            raise AppException(
                message="Username or email already exists",
                code="USER_CONFLICT",
                status_code=409,
            ) from exc

        await self.db.refresh(user)
        return user

    async def authenticate(self, email: str, password: str) -> User:
        """邮箱 + 口令，校验通过返回用户，否则统一抛 401。"""
        # 邮箱存库时统一小写，登录时同样归一化；库的 ci collation 再兜一层大小写
        stmt = select(User).where(User.email == email.strip().lower())
        user = await self.db.scalar(stmt)

        if user is None:
            verify_password(password, _dummy_password_hash())
            raise _invalid_credentials()

        if not verify_password(password, user.hashed_password):
            raise _invalid_credentials()

        # 口令正确才提示账号被禁用：先验口令再报状态，避免不掌握口令也能探测账号状态
        if not user.is_active:
            raise AppException(
                message="Account is disabled",
                code="ACCOUNT_DISABLED",
                status_code=403,
            )

        return user

    async def get_user_by_id(self, user_id: int) -> User | None:
        """按主键取用户；不存在返回 None，由调用方决定 401 还是 404。"""
        return await self.db.scalar(select(User).where(User.id == user_id))

    async def _exists(self, *, username: str | None = None, email: str | None = None) -> bool:
        """按唯一字段查重；走唯一索引，代价等同于一次点查。"""
        conditions = []
        if username is not None:
            conditions.append(User.username == username)
        if email is not None:
            conditions.append(User.email == email)

        if not conditions:
            return False

        result = await self.db.execute(select(User.id).where(*conditions).limit(1))
        return result.scalar_one_or_none() is not None
