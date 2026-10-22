from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.core.security import (
    BEARER_CHALLENGE_HEADERS,
    REFRESH_TOKEN_TYPE,
    create_access_token,
    create_refresh_token,
    decode_token,
)
from app.models.token import RevokedToken
from app.models.user import User
from app.schemas.user import TokenResponse
from app.services.user_service import UserService


def _naive_utc(timestamp: int) -> datetime:
    """JWT 的 exp 是 UTC 秒级时间戳；库里的 DATETIME 不带时区，统一存 naive UTC。"""
    return datetime.fromtimestamp(timestamp, UTC).replace(tzinfo=None)


class TokenService:
    def __init__(self, db: AsyncSession):
        self.db = db

    def issue_pair(self, user: User) -> TokenResponse:
        """签发一对令牌：access 访问业务，refresh 只用来换新的一对。"""
        access = create_access_token(str(user.id))
        refresh = create_refresh_token(str(user.id))

        return TokenResponse(
            access_token=access.token,
            refresh_token=refresh.token,
            expires_in=access.expires_in,
        )

    async def is_revoked(self, jti: str) -> bool:
        """每次带 token 的请求都会查一次；jti 上有唯一索引，等价于一次点查。"""
        stmt = select(RevokedToken.id).where(RevokedToken.jti == jti).limit(1)
        return await self.db.scalar(stmt) is not None

    async def revoke(self, *tokens: tuple[str, int]) -> None:
        """把 (jti, exp) 记入撤销表；已存在的跳过，顺带清理自然过期的行。"""
        for jti, exp in tokens:
            if await self.is_revoked(jti):
                continue
            self.db.add(RevokedToken(jti=jti, expires_at=_naive_utc(exp)))

        await self._purge_expired()
        try:
            await self.db.commit()
        except IntegrityError:
            # 同一令牌并发登出：另一路已经写入，效果等价于已撤销
            await self.db.rollback()

    async def refresh(self, refresh_token: str) -> TokenResponse:
        """用刷新令牌换一对新令牌；旧 refresh 立即作废（轮换）。"""
        payload = decode_token(refresh_token, expected_type=REFRESH_TOKEN_TYPE)
        jti = str(payload["jti"])

        if await self.is_revoked(jti):
            raise AppException(
                message="Token has been revoked",
                code="TOKEN_REVOKED",
                status_code=401,
                headers=BEARER_CHALLENGE_HEADERS,
            )

        user = await self._load_user(payload)
        if not user.is_active:
            raise AppException(
                message="Account is disabled",
                code="ACCOUNT_DISABLED",
                status_code=403,
            )

        # 先作废旧 refresh 再签发：重放同一个 refresh 会命中撤销表，属于可疑行为
        await self.revoke((jti, int(payload["exp"])))

        return self.issue_pair(user)

    async def _load_user(self, payload: dict) -> User:
        try:
            user_id = int(str(payload["sub"]))
        except (TypeError, ValueError) as exc:
            raise AppException(
                message="Invalid authentication token",
                code="INVALID_TOKEN",
                status_code=401,
                headers=BEARER_CHALLENGE_HEADERS,
            ) from exc

        user = await UserService(self.db).get_user_by_id(user_id)
        if user is None:
            raise AppException(
                message="User no longer exists",
                code="USER_NOT_FOUND",
                status_code=401,
                headers=BEARER_CHALLENGE_HEADERS,
            )

        return user

    async def _purge_expired(self) -> None:
        """撤销表只对「尚未自然过期」的令牌有意义，顺手清掉过期行避免无限增长。"""
        await self.db.execute(
            delete(RevokedToken).where(RevokedToken.expires_at < datetime.now(UTC).replace(tzinfo=None))
        )
