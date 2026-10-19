from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.core.security import BCRYPT_MAX_PASSWORD_BYTES, hash_password
from app.models.user import User
from app.schemas.user import UserCreate


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
