from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class RevokedToken(Base):
    """已撤销的令牌：只记 jti 与过期时间，不存令牌原文。

    令牌原文再泄露也没用——撤销表查的是 jti，且这行数据本身就是「已作废」的标记。
    """

    __tablename__ = "revoked_tokens"

    id: Mapped[int] = mapped_column(
        primary_key=True,
        autoincrement=True,
    )

    jti: Mapped[str] = mapped_column(
        String(36),
        nullable=False,
        unique=True,
        index=True,
    )

    # 令牌自身的过期时刻：过了这一刻这行就没意义了，可安全清理
    expires_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        index=True,
    )

    revoked_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )
