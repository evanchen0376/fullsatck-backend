"""注册 / 登录 / JWT 鉴权的接口级测试。

覆盖验收清单：
- 邮箱 + 密码登录，返回 Access Token
- JWT 载荷含 sub / iat / exp / iss / aud
- 携带 Bearer Token 可调用 GET /api/v1/auth/me
- 无 Token、错误密码、过期或伪造 Token 均返回 401
"""

from datetime import timedelta
from typing import Any

import jwt
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import create_access_token
from app.models.user import User

AUTH_URL = "/api/v1/auth"

pytestmark = pytest.mark.anyio


def register_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "username": "alice",
        "email": "alice@example.com",
        "password": "secret123",
    }
    payload.update(overrides)
    return payload


async def register(client: AsyncClient, **overrides: Any) -> dict[str, Any]:
    response = await client.post(f"{AUTH_URL}/register", json=register_payload(**overrides))
    assert response.status_code == 201, response.text
    return response.json()["data"]


async def login(
    client: AsyncClient,
    email: str = "alice@example.com",
    password: str = "secret123",
) -> Any:
    return await client.post(f"{AUTH_URL}/login", json={"email": email, "password": password})


async def login_headers(
    client: AsyncClient,
    email: str = "alice@example.com",
    password: str = "secret123",
) -> dict[str, str]:
    """登录并返回可直接塞进请求头的 Authorization。"""
    response = await login(client, email=email, password=password)
    assert response.status_code == 200, response.text

    return {"Authorization": f"Bearer {response.json()['data']['access_token']}"}


class TestLogin:
    async def test_returns_access_token(self, client: AsyncClient) -> None:
        await register(client)

        response = await login(client)

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["code"] == "OK"
        data = body["data"]
        assert data["token_type"] == "bearer"
        assert data["access_token"].count(".") == 2
        assert data["expires_in"] == settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60

    async def test_token_contains_required_claims(self, client: AsyncClient) -> None:
        user = await register(client)
        response = await login(client)

        token = response.json()["data"]["access_token"]
        claims = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            audience=settings.JWT_AUDIENCE,
            issuer=settings.JWT_ISSUER,
        )

        assert claims["sub"] == str(user["id"])
        assert claims["iss"] == settings.JWT_ISSUER
        assert claims["aud"] == settings.JWT_AUDIENCE
        # iat/exp 由 PyJWT 编码成数值时间戳（秒），exp 必须晚于 iat
        assert claims["iat"] < claims["exp"]
        assert claims["exp"] - claims["iat"] == settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60

    async def test_email_is_case_insensitive(self, client: AsyncClient) -> None:
        await register(client, email="Alice@Example.com")

        response = await login(client, email="ALICE@example.com")

        assert response.status_code == 200, response.text

    async def test_wrong_password_returns_401(self, client: AsyncClient) -> None:
        await register(client)

        response = await login(client, password="wrong-password")

        assert response.status_code == 401
        assert response.json()["code"] == "INVALID_CREDENTIALS"
        assert response.headers["www-authenticate"] == "Bearer"

    async def test_unknown_email_returns_401(self, client: AsyncClient) -> None:
        response = await login(client, email="nobody@example.com")

        assert response.status_code == 401
        # 与口令错误共用同一错误码，不泄露邮箱是否已注册
        assert response.json()["code"] == "INVALID_CREDENTIALS"

    async def test_disabled_account_returns_403(self, client: AsyncClient, db_session: AsyncSession) -> None:
        user = await register(client)
        db_user = await db_session.get(User, user["id"])
        db_user.is_active = False
        await db_session.flush()

        response = await login(client)

        assert response.status_code == 403
        assert response.json()["code"] == "ACCOUNT_DISABLED"


class TestAuthMe:
    async def test_bearer_token_returns_current_user(self, client: AsyncClient) -> None:
        user = await register(client)
        headers = await login_headers(client)

        response = await client.get(f"{AUTH_URL}/me", headers=headers)

        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["id"] == user["id"]
        assert data["email"] == "alice@example.com"
        # 响应模型不含口令哈希，即使 ORM 对象上有这个字段
        assert "hashed_password" not in data
        assert "password" not in data

    async def test_missing_token_returns_401(self, client: AsyncClient) -> None:
        response = await client.get(f"{AUTH_URL}/me")

        assert response.status_code == 401
        assert response.json()["code"] == "NOT_AUTHENTICATED"
        assert response.headers["www-authenticate"] == "Bearer"

    async def test_malformed_token_returns_401(self, client: AsyncClient) -> None:
        response = await client.get(f"{AUTH_URL}/me", headers={"Authorization": "Bearer not-a-jwt"})

        assert response.status_code == 401
        assert response.json()["code"] == "INVALID_TOKEN"

    async def test_expired_token_returns_401(self, client: AsyncClient) -> None:
        user = await register(client)
        token, _ = create_access_token(str(user["id"]), expires_delta=timedelta(seconds=-1))

        response = await client.get(f"{AUTH_URL}/me", headers={"Authorization": f"Bearer {token}"})

        assert response.status_code == 401
        assert response.json()["code"] == "TOKEN_EXPIRED"

    async def test_forged_token_returns_401(self, client: AsyncClient) -> None:
        user = await register(client)
        forged = jwt.encode(
            {
                "sub": str(user["id"]),
                "iat": 1,
                "exp": 9999999999,
                "iss": settings.JWT_ISSUER,
                "aud": settings.JWT_AUDIENCE,
                "type": "access",
            },
            "a-different-secret-key-that-is-long-enough",
            algorithm=settings.JWT_ALGORITHM,
        )

        response = await client.get(f"{AUTH_URL}/me", headers={"Authorization": f"Bearer {forged}"})

        assert response.status_code == 401
        assert response.json()["code"] == "INVALID_TOKEN"

    async def test_wrong_audience_returns_401(self, client: AsyncClient) -> None:
        user = await register(client)
        token = jwt.encode(
            {
                "sub": str(user["id"]),
                "iat": 1,
                "exp": 9999999999,
                "iss": settings.JWT_ISSUER,
                "aud": "some-other-api",
                "type": "access",
            },
            settings.SECRET_KEY,
            algorithm=settings.JWT_ALGORITHM,
        )

        response = await client.get(f"{AUTH_URL}/me", headers={"Authorization": f"Bearer {token}"})

        assert response.status_code == 401
        assert response.json()["code"] == "INVALID_TOKEN"

    async def test_disabled_account_returns_403(self, client: AsyncClient, db_session: AsyncSession) -> None:
        user = await register(client)
        headers = await login_headers(client)

        db_user = await db_session.get(User, user["id"])
        db_user.is_active = False
        await db_session.flush()

        response = await client.get(f"{AUTH_URL}/me", headers=headers)

        assert response.status_code == 403
        assert response.json()["code"] == "ACCOUNT_DISABLED"
