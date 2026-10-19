"""RBAC 与 401/403 语义测试。

要点：
- 未认证（无 token / token 非法）→ 401，并带 WWW-Authenticate
- 已认证但角色不够 → 403，不能说成 401，否则客户端会去重新登录
- 角色以数据库为准而非令牌，改角色立即生效，不需要重新签发 token
"""

from typing import Any

import jwt
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.user import User, UserRole

AUTH_URL = "/api/v1/auth"
ADMIN_URL = "/api/v1/admin"

pytestmark = pytest.mark.anyio


async def register(
    client: AsyncClient,
    username: str = "alice",
    email: str = "alice@example.com",
) -> dict[str, Any]:
    response = await client.post(
        f"{AUTH_URL}/register",
        json={"username": username, "email": email, "password": "secret123"},
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]


async def login_headers(client: AsyncClient, email: str = "alice@example.com") -> dict[str, str]:
    response = await client.post(f"{AUTH_URL}/login", json={"email": email, "password": "secret123"})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['data']['access_token']}"}


async def grant_admin(db_session: AsyncSession, user_id: int) -> None:
    """直接改库授予管理员：真实系统里应由运维脚本/初始化数据完成，不开放自助提权接口。"""
    user = await db_session.get(User, user_id)
    user.role = UserRole.ADMIN
    await db_session.flush()


async def create_admin(client: AsyncClient, db_session: AsyncSession, **kwargs: Any) -> dict[str, str]:
    user = await register(client, **kwargs)
    await grant_admin(db_session, user["id"])
    return await login_headers(client, email=user["email"])


class TestRoleErrors:
    async def test_missing_token_returns_401(self, client: AsyncClient) -> None:
        response = await client.get(f"{ADMIN_URL}/users")

        assert response.status_code == 401
        assert response.json()["code"] == "NOT_AUTHENTICATED"
        assert response.headers["www-authenticate"] == "Bearer"

    async def test_invalid_token_returns_401(self, client: AsyncClient) -> None:
        response = await client.get(f"{ADMIN_URL}/users", headers={"Authorization": "Bearer garbage"})

        assert response.status_code == 401
        assert response.json()["code"] == "INVALID_TOKEN"

    async def test_normal_user_returns_403(self, client: AsyncClient, db_session: AsyncSession) -> None:
        await register(client)
        headers = await login_headers(client)

        response = await client.get(f"{ADMIN_URL}/users", headers=headers)

        # 关键：身份已认证，只是没权限 → 403，且不带 WWW-Authenticate
        assert response.status_code == 403
        assert response.json()["code"] == "FORBIDDEN"
        assert "www-authenticate" not in response.headers


class TestAdminUsers:
    async def test_admin_can_list_users(self, client: AsyncClient, db_session: AsyncSession) -> None:
        await register(client, username="alice", email="alice@example.com")
        await register(client, username="bob", email="bob@example.com")
        headers = await create_admin(client, db_session, username="root", email="root@example.com")

        response = await client.get(f"{ADMIN_URL}/users", headers=headers)

        assert response.status_code == 200, response.text
        page = response.json()["data"]
        assert page["total"] == 3
        assert {item["username"] for item in page["items"]} == {"alice", "bob", "root"}
        assert all("hashed_password" not in item for item in page["items"])

    async def test_promotion_takes_effect_with_existing_token(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        target = await register(client, username="bob", email="bob@example.com")
        user_headers = await login_headers(client, email="bob@example.com")

        assert (await client.get(f"{ADMIN_URL}/users", headers=user_headers)).status_code == 403

        admin_headers = await create_admin(client, db_session, username="root", email="root@example.com")
        response = await client.patch(
            f"{ADMIN_URL}/users/{target['id']}",
            json={"role": "admin"},
            headers=admin_headers,
        )
        assert response.status_code == 200, response.text
        assert response.json()["data"]["role"] == "admin"

        # 用的是升级前的旧 token：角色来自数据库，所以立即生效
        assert (await client.get(f"{ADMIN_URL}/users", headers=user_headers)).status_code == 200

    async def test_token_does_not_carry_role(self, client: AsyncClient) -> None:
        await register(client)
        response = await client.post(f"{AUTH_URL}/login", json={"email": "alice@example.com", "password": "secret123"})
        token = response.json()["data"]["access_token"]

        claims = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.JWT_ALGORITHM], audience=settings.JWT_AUDIENCE
        )

        # 角色不进令牌：否则降权要等令牌过期才生效
        assert "role" not in claims

    async def test_disable_user_blocks_me_and_login(self, client: AsyncClient, db_session: AsyncSession) -> None:
        target = await register(client, username="bob", email="bob@example.com")
        user_headers = await login_headers(client, email="bob@example.com")
        admin_headers = await create_admin(client, db_session, username="root", email="root@example.com")

        response = await client.patch(
            f"{ADMIN_URL}/users/{target['id']}",
            json={"is_active": False},
            headers=admin_headers,
        )
        assert response.status_code == 200, response.text
        assert response.json()["data"]["is_active"] is False

        assert (await client.get(f"{AUTH_URL}/me", headers=user_headers)).status_code == 403

        login = await client.post(f"{AUTH_URL}/login", json={"email": "bob@example.com", "password": "secret123"})
        assert login.status_code == 403
        assert login.json()["code"] == "ACCOUNT_DISABLED"

    async def test_admin_cannot_modify_self(self, client: AsyncClient, db_session: AsyncSession) -> None:
        admin = await register(client, username="root", email="root@example.com")
        await grant_admin(db_session, admin["id"])
        headers = await login_headers(client, email="root@example.com")

        response = await client.patch(f"{ADMIN_URL}/users/{admin['id']}", json={"role": "user"}, headers=headers)

        assert response.status_code == 403
        assert response.json()["code"] == "SELF_MODIFICATION_FORBIDDEN"

    async def test_update_missing_user_returns_404(self, client: AsyncClient, db_session: AsyncSession) -> None:
        headers = await create_admin(client, db_session, username="root", email="root@example.com")

        response = await client.patch(f"{ADMIN_URL}/users/999999", json={"is_active": False}, headers=headers)

        assert response.status_code == 404
        assert response.json()["code"] == "USER_NOT_FOUND"

    async def test_update_without_fields_returns_422(self, client: AsyncClient, db_session: AsyncSession) -> None:
        target = await register(client, username="bob", email="bob@example.com")
        headers = await create_admin(client, db_session, username="root", email="root@example.com")

        response = await client.patch(f"{ADMIN_URL}/users/{target['id']}", json={}, headers=headers)

        assert response.status_code == 422
        assert response.json()["code"] == "VALIDATION_ERROR"

    async def test_registration_ignores_role_field(self, client: AsyncClient) -> None:
        response = await client.post(
            f"{AUTH_URL}/register",
            json={
                "username": "mallory",
                "email": "mallory@example.com",
                "password": "secret123",
                "role": "admin",
            },
        )

        assert response.status_code == 201, response.text
        # 提权字段被 UserCreate 忽略，落在库里的仍是普通用户
        assert response.json()["data"]["role"] == "user"


class TestAdminSummary:
    async def test_missing_token_returns_401(self, client: AsyncClient) -> None:
        response = await client.get(f"{ADMIN_URL}/summary")

        assert response.status_code == 401
        assert response.json()["code"] == "NOT_AUTHENTICATED"

    async def test_normal_user_returns_403(self, client: AsyncClient) -> None:
        await register(client)
        headers = await login_headers(client)

        response = await client.get(f"{ADMIN_URL}/summary", headers=headers)

        assert response.status_code == 403
        assert response.json()["code"] == "FORBIDDEN"

    async def test_admin_returns_200_with_counts(self, client: AsyncClient, db_session: AsyncSession) -> None:
        await register(client, username="alice", email="alice@example.com")
        await register(client, username="bob", email="bob@example.com")
        headers = await create_admin(client, db_session, username="root", email="root@example.com")

        created = await client.post("/api/v1/products", json={"name": "机械键盘", "price": "299.00", "stock": 10})
        assert created.status_code == 201, created.text

        response = await client.get(f"{ADMIN_URL}/summary", headers=headers)

        assert response.status_code == 200, response.text
        assert response.json()["data"] == {
            "users": {"total": 3, "active": 3, "admins": 1},
            "products": {"total": 1, "total_stock": 10},
        }

    async def test_summary_without_products_returns_zero_stock(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers = await create_admin(client, db_session, username="root", email="root@example.com")

        response = await client.get(f"{ADMIN_URL}/summary", headers=headers)

        # 空表时 SUM 为 NULL，必须 coalesce 成 0 而不是返回 null
        assert response.json()["data"]["products"] == {"total": 0, "total_stock": 0}

    async def test_demotion_revokes_admin_for_old_token(self, client: AsyncClient, db_session: AsyncSession) -> None:
        target = await register(client, username="bob", email="bob@example.com")
        # 先以普通用户身份登录，拿到一个「久经考验」的旧 token
        target_headers = await login_headers(client, email="bob@example.com")
        root_headers = await create_admin(client, db_session, username="root", email="root@example.com")

        promoted = await client.patch(f"{ADMIN_URL}/users/{target['id']}", json={"role": "admin"}, headers=root_headers)
        assert promoted.status_code == 200, promoted.text
        assert (await client.get(f"{ADMIN_URL}/summary", headers=target_headers)).status_code == 200

        demoted = await client.patch(f"{ADMIN_URL}/users/{target['id']}", json={"role": "user"}, headers=root_headers)
        assert demoted.status_code == 200, demoted.text

        # 同一个旧 token：权限没了，但身份还在
        denied = await client.get(f"{ADMIN_URL}/summary", headers=target_headers)
        assert denied.status_code == 403
        assert denied.json()["code"] == "FORBIDDEN"

        assert (await client.get(f"{AUTH_URL}/me", headers=target_headers)).status_code == 200
