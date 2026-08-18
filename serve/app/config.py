from urllib.parse import quote_plus
from pydantic_settings import BaseSettings

BASE_DATABASE_NAME = "base_platform"
BASE_DATABASE_USER = "base_platform_app"


class Settings(BaseSettings):
    # 应用
    APP_NAME: str = "base"
    APP_URL: str = "http://localhost:3000"
    APP_KEY: str = ""
    APP_DEBUG: bool = False
    PORT: int = 3000
    WORKERS: int = 0

    # PostgreSQL
    DATABASE_HOST: str = "localhost"
    DATABASE_PORT: int = 5432
    DATABASE_NAME: str = BASE_DATABASE_NAME
    DATABASE_USER: str = BASE_DATABASE_USER
    DATABASE_PASSWORD: str = ""
    DATABASE_SCHEMA: str = "public"
    DATABASE_POOL_SIZE: int = 20
    DATABASE_MAX_OVERFLOW: int = 10

    # Redis
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_PASSWORD: str = ""

    # CORS
    CORS_ORIGINS: str = "*"              # 允许的源，逗号分隔，"*"=全部

    # Token
    TOKEN_EXPIRES_IN: int = 7200  # access_token 有效期（秒，默认 2 小时）
    REFRESH_TOKEN_EXPIRES_IN: int = 604800  # refresh_token 有效期（秒，默认 7 天）

    @property
    def database_url(self) -> str:
        password = quote_plus(self.DATABASE_PASSWORD)
        return (
            f"postgresql+asyncpg://{self.DATABASE_USER}:{password}"
            f"@{self.DATABASE_HOST}:{self.DATABASE_PORT}/{self.DATABASE_NAME}"
        )

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
