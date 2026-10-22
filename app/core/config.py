from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL


class Settings(BaseSettings):
    MYSQL_HOST: str = "127.0.0.1"
    MYSQL_PORT: int = 3306
    MYSQL_USER: str = "root"
    MYSQL_PASSWORD: str = ""
    MYSQL_DATABASE: str = "minishop"

    # 生产必须通过环境变量覆盖：默认值只用于本地开发，泄露它等于可以伪造任意用户令牌
    SECRET_KEY: str = "dev-only-insecure-secret-key-change-me"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    # 刷新令牌活得比访问令牌久，但每次刷新都会轮换（旧的立即作废）
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    # iss/aud 让令牌只能被本服务、本 API 使用，防止同一密钥签出的令牌被别的系统复用
    JWT_ISSUER: str = "minishop"
    JWT_AUDIENCE: str = "minishop-api"

    # 登录限流：同一 IP + 同一账号，窗口内允许的失败次数
    LOGIN_MAX_ATTEMPTS: int = 5
    LOGIN_LOCKOUT_SECONDS: int = 900

    # 限流计数必须多实例共享，所以落在 Redis 而不是进程内存
    REDIS_URL: str = "redis://127.0.0.1:6379/0"
    # 前缀用于隔离环境/测试：清库时只删自己的 key，不误伤同一实例上的其它数据
    RATE_LIMIT_KEY_PREFIX: str = "minishop:ratelimit:login"
    # Redis 故障时的取舍：True 放行（可用性优先），False 拒绝（安全优先，登录会整体不可用）
    RATE_LIMIT_FAIL_OPEN: bool = True
    # 限流判定不能让登录被 Redis 拖住：连接/读写都必须有短超时
    REDIS_TIMEOUT_SECONDS: float = 0.5

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )

    @property
    def database_url(self) -> str:
        url = URL.create(
            drivername="mysql+asyncmy",
            username=self.MYSQL_USER,
            password=self.MYSQL_PASSWORD,
            host=self.MYSQL_HOST,
            port=self.MYSQL_PORT,
            database=self.MYSQL_DATABASE,
            query={"charset": "utf8mb4"},
        )

        return url.render_as_string(hide_password=False)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
