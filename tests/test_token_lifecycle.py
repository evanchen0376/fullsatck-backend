"""令牌生命周期：刷新轮换、登出撤销、账户停用、登录限流。

这四件事共享同一个前提：令牌能被「提前作废」。
access 只活 60 分钟不足以覆盖登出场景，所以撤销表 + jti 是整套机制的支点。
"""

from datetime import timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_refresh_token
from app.models.user import User

AUTH_URL = "/api/v1/auth"

pytestmark = pytest.mark.anyio


async def register(client: AsyncClient, email: str = "alice@example.com") -> dict[str, Any]:
    response = await client.post(
        f"{AUTH_URL}/register",
        json={"username": email.split("@")[0], "email": email, "password": "secret123"},
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]


async def login(
    client: AsyncClient,
    email: str = "alice@example.com",
    password: str = "secret123",
) -> dict[str, Any]:
    response = await client.post(f"{AUTH_URL}/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def attempt(client: AsyncClient, email: str, password: str) -> Any:
    """不带断言的登录，用于专门测失败路径。"""
    return await client.post(f"{AUTH_URL}/login", json={"email": email, "password": password})


def bearer(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


async def refresh(client: AsyncClient, refresh_token: str) -> Any:
    return await client.post(f"{AUTH_URL}/refresh", json={"refresh_token": refresh_token})


async def logout(client: AsyncClient, access_token: str, refresh_token: str) -> Any:
    return await client.post(
        f"{AUTH_URL}/logout",
        json={"refresh_token": refresh_token},
        headers=bearer(access_token),
    )


class TestLoginIssuesPair:
    async def test_login_returns_both_tokens(self, client: AsyncClient) -> None:
        await register(client)
        tokens = await login(client)

        assert tokens["access_token"] != tokens["refresh_token"]
        assert tokens["expires_in"] == 3600


class TestRefresh:
    async def test_refresh_returns_new_pair(self, client: AsyncClient) -> None:
        await register(client)
        tokens = await login(client)

        response = await refresh(client, tokens["refresh_token"])

        assert response.status_code == 200, response.text
        new_tokens = response.json()["data"]
        assert new_tokens["access_token"] != tokens["access_token"]
        assert new_tokens["refresh_token"] != tokens["refresh_token"]

        # 新的 access 立即可用
        me = await client.get(f"{AUTH_URL}/me", headers=bearer(new_tokens["access_token"]))
        assert me.status_code == 200

    async def test_old_refresh_is_rotated(self, client: AsyncClient) -> None:
        await register(client)
        tokens = await login(client)

        assert (await refresh(client, tokens["refresh_token"])).status_code == 200
        # 同一个 refresh 再用一次：轮换后已进撤销表，视为重放
        replay = await refresh(client, tokens["refresh_token"])

        assert replay.status_code == 401
        assert replay.json()["code"] == "TOKEN_REVOKED"

    async def test_access_token_cannot_be_used_as_refresh(self, client: AsyncClient) -> None:
        await register(client)
        tokens = await login(client)

        response = await refresh(client, tokens["access_token"])

        assert response.status_code == 401
        assert response.json()["code"] == "INVALID_TOKEN"

    async def test_refresh_token_cannot_access_protected_route(self, client: AsyncClient) -> None:
        await register(client)
        tokens = await login(client)

        response = await client.get(f"{AUTH_URL}/me", headers=bearer(tokens["refresh_token"]))

        assert response.status_code == 401
        assert response.json()["code"] == "INVALID_TOKEN"

    async def test_expired_refresh_returns_401(self, client: AsyncClient) -> None:
        user = await register(client)
        expired = create_refresh_token(str(user["id"]), expires_delta=timedelta(seconds=-1))

        response = await refresh(client, expired.token)

        assert response.status_code == 401
        assert response.json()["code"] == "TOKEN_EXPIRED"

    async def test_garbage_refresh_returns_401(self, client: AsyncClient) -> None:
        response = await refresh(client, "not-a-jwt")

        assert response.status_code == 401
        assert response.json()["code"] == "INVALID_TOKEN"

    async def test_disabled_user_cannot_refresh(self, client: AsyncClient, db_session: AsyncSession) -> None:
        user = await register(client)
        tokens = await login(client)

        db_user = await db_session.get(User, user["id"])
        db_user.is_active = False
        await db_session.flush()

        response = await refresh(client, tokens["refresh_token"])

        assert response.status_code == 403
        assert response.json()["code"] == "ACCOUNT_DISABLED"


class TestLogout:
    async def test_logout_revokes_access_and_refresh(self, client: AsyncClient) -> None:
        await register(client)
        tokens = await login(client)

        response = await logout(client, tokens["access_token"], tokens["refresh_token"])
        assert response.status_code == 200, response.text

        # access 立刻失效
        me = await client.get(f"{AUTH_URL}/me", headers=bearer(tokens["access_token"]))
        assert me.status_code == 401
        assert me.json()["code"] == "TOKEN_REVOKED"

        # refresh 也不能再换新令牌，否则等于没登出
        rotated = await refresh(client, tokens["refresh_token"])
        assert rotated.status_code == 401
        assert rotated.json()["code"] == "TOKEN_REVOKED"

    async def test_logout_requires_authentication(self, client: AsyncClient) -> None:
        await register(client)
        tokens = await login(client)

        response = await client.post(
            f"{AUTH_URL}/logout",
            json={"refresh_token": tokens["refresh_token"]},
        )

        assert response.status_code == 401
        assert response.json()["code"] == "NOT_AUTHENTICATED"

    async def test_cannot_logout_with_someone_elses_refresh(self, client: AsyncClient) -> None:
        await register(client, email="alice@example.com")
        await register(client, email="bob@example.com")
        alice = await login(client, email="alice@example.com")
        bob = await login(client, email="bob@example.com")

        response = await logout(client, alice["access_token"], bob["refresh_token"])

        assert response.status_code == 400
        assert response.json()["code"] == "INVALID_REFRESH_TOKEN"

        # 失败不产生副作用：Alice 自己仍然登录着，Bob 也不受影响
        assert (await client.get(f"{AUTH_URL}/me", headers=bearer(alice["access_token"]))).status_code == 200
        assert (await refresh(client, bob["refresh_token"])).status_code == 200

    async def test_double_logout_returns_401(self, client: AsyncClient) -> None:
        await register(client)
        tokens = await login(client)

        assert (await logout(client, tokens["access_token"], tokens["refresh_token"])).status_code == 200
        again = await logout(client, tokens["access_token"], tokens["refresh_token"])

        assert again.status_code == 401
        assert again.json()["code"] == "TOKEN_REVOKED"


class TestLoginRateLimit:
    async def test_failures_trigger_429_with_retry_after(self, client: AsyncClient) -> None:
        await register(client, email="limit1@example.com")

        for _ in range(5):
            response = await attempt(client, "limit1@example.com", "wrong-password")
            assert response.status_code == 401

        # 第 6 次即使口令正确也先被限流拦住
        blocked = await attempt(client, "limit1@example.com", "secret123")
        assert blocked.status_code == 429
        assert blocked.json()["code"] == "TOO_MANY_ATTEMPTS"
        assert int(blocked.headers["retry-after"]) > 0

    async def test_rate_limit_is_case_insensitive_on_email(self, client: AsyncClient) -> None:
        await register(client, email="limit2@example.com")

        for _ in range(5):
            await attempt(client, "LIMIT2@example.com", "wrong-password")

        blocked = await attempt(client, "limit2@example.com", "secret123")
        assert blocked.status_code == 429

    async def test_other_account_is_not_blocked(self, client: AsyncClient) -> None:
        await register(client, email="limit3@example.com")
        await register(client, email="limit4@example.com")

        for _ in range(5):
            await attempt(client, "limit3@example.com", "wrong-password")

        tokens = await login(client, email="limit4@example.com")  # 内部断言 200

        assert "refresh_token" in tokens

    async def test_success_resets_counter(self, client: AsyncClient) -> None:
        await register(client, email="limit5@example.com")

        for _ in range(4):
            await attempt(client, "limit5@example.com", "wrong-password")

        # 第 5 次成功：计数清零
        await login(client, email="limit5@example.com")

        # 若没清零，这里第 5 次失败就会触发 429
        for _ in range(4):
            response = await attempt(client, "limit5@example.com", "wrong-password")
            assert response.status_code == 401
