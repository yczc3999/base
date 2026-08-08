"""
应用配置 — typed 基础设施 env 配置（V2 交易域使用，扩展 Base 静态配置）。

三类配置严格分离（见 serve/docs/polymarket-v2-platform-design.md §3.1）：
1. 基础设施配置（本文件）：数据库、Redis、KMS、静态服务凭据，只来自 server env/secret。
2. 策略配置：R0/R1、模型角色、prompt、证据、收缩、quote TTL、edge、成本与风险，
   使用不可变版本化对象，任务入队时固定 release_manifest_id；不在此文件。
3. 资金权限：shadow/canary/live、授权资本、类别/component/global cap、kill switch，
   由独立 capital_permission_manifest 管理；不在此文件。

本文件禁止出现：策略参数、资本权限、secret 明文、运行中 latest 配置、业务逻辑。
API key / Token / Cookie 只以 secret_ref + secret_version 形式出现，凭据由 vault 解密。
"""

from typing import Mapping
from urllib.parse import quote, quote_plus

from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings


# 六个独立进程 profile（performance-cache-database-design.md §8.3 / WP-00a 固定集合）。
# 每个 profile 映射到 Settings 上的三个扁平 env 字段：(pool_size, max_overflow, statement_timeout_s)
PROFILE_FIELDS: dict[str, tuple[str, str, str]] = {
    "api": ("DB_API_POOL_SIZE", "DB_API_POOL_OVERFLOW", "DB_API_STMT_TIMEOUT_S"),
    "market": ("DB_MARKET_POOL_SIZE", "DB_MARKET_POOL_OVERFLOW", "DB_MARKET_STMT_TIMEOUT_S"),
    "execution": ("DB_EXECUTION_POOL_SIZE", "DB_EXECUTION_POOL_OVERFLOW", "DB_EXECUTION_STMT_TIMEOUT_S"),
    "cognition": ("DB_COGNITION_POOL_SIZE", "DB_COGNITION_POOL_OVERFLOW", "DB_COGNITION_STMT_TIMEOUT_S"),
    "evaluation": ("DB_EVALUATION_POOL_SIZE", "DB_EVALUATION_POOL_OVERFLOW", "DB_EVALUATION_STMT_TIMEOUT_S"),
    "replay": ("DB_REPLAY_POOL_SIZE", "DB_REPLAY_POOL_OVERFLOW", "DB_REPLAY_STMT_TIMEOUT_S"),
}


class PoolProfile(BaseModel):
    """
    单个进程的数据库连接池 profile（typed）。

    只描述基础设施参数，不含任何策略/资金/业务语义。
    statement_timeout / lock_timeout / idle_in_transaction_session_timeout
    最终通过 asyncpg server_settings 以毫秒为单位下发（见 services/database.py）。
    """

    name: str
    pool_size: int = Field(ge=1, description="池内常驻连接数")
    max_overflow: int = Field(ge=0, description="超出 pool_size 的临时连接数上限")
    statement_timeout_s: int = Field(ge=1, description="本进程 SQL statement 超时（秒）")
    pre_ping: bool = True

    @property
    def application_name(self) -> str:
        """PostgreSQL application_name，便于按进程归类连接。"""
        return f"pollymarket_v2_{self.name}"

    @property
    def per_instance_capacity(self) -> int:
        """单实例连接上限（不含副本缩放）。"""
        return self.pool_size + self.max_overflow


class ConnectionBudget(BaseModel):
    """
    全局连接预算（整个部署，跨所有副本）。

    公式：Σ(profile_replica_count × (pool_size + max_overflow))
        ≤ DB_MAX_CONNECTIONS − DB_ADMIN_RESERVED_CONNECTIONS
    """

    per_profile: dict[str, int]
    replica_counts: dict[str, int]
    total: int
    max_connections: int
    reserved: int
    limit: int
    remaining: int

    def is_within_limit(self) -> bool:
        return self.total <= self.limit


class RedisEndpoint(BaseModel):
    """
    单个 Redis 角色的 typed 连接配置（基础设施，performance 设计 §5.1/§14）。

    namespace 是键前缀，必须包含 env + schema_version（§5.2）。
    """

    url: str
    max_connections: int = Field(ge=1, description="连接池上限")
    connect_timeout_s: float = Field(gt=0, description="建立连接超时（秒）")
    read_timeout_s: float = Field(gt=0, description="命令读超时（秒）")
    health_check_interval_s: float = Field(gt=0, description="空闲连接健康检查间隔（秒）")
    namespace: str = Field(min_length=1, description="键前缀，形如 pm:v2:prod:control")


class ControlRedisEndpoint(RedisEndpoint):
    """Control Redis：fail-closed，故障时禁止增仓。"""


class CacheRedisEndpoint(RedisEndpoint):
    """Cache Redis：可丢热点投影，故障只降级不改变业务判断。"""

    default_ttl_s: int = Field(ge=1, description="默认有限 TTL（秒）；永久 TTL 禁止")
    ttl_jitter_s: int = Field(ge=0, description="TTL 抖动上限（秒），[0, jitter) 均匀")
    bypass_on_error: bool = Field(default=True, description="连接故障时吞错降级（get→None、写→False）")


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
    DATABASE_NAME: str = "base"
    DATABASE_USER: str = "base_user"
    DATABASE_PASSWORD: str = ""
    DATABASE_SCHEMA: str = "public"

    # ---- V2 连接池全局参数（performance-cache-database-design.md §8.3）----
    DB_MAX_CONNECTIONS: int = Field(100, ge=1, description="PostgreSQL max_connections")
    DB_ADMIN_RESERVED_CONNECTIONS: int = Field(20, ge=0, description="管理/迁移保留连接数")
    DB_POOL_PRE_PING: bool = True
    DB_POOL_TIMEOUT_S: float = Field(3.0, gt=0, description="pool 等待连接超时（秒）")
    DB_POOL_RECYCLE_S: int = Field(1800, gt=0, description="连接回收周期（秒）")
    DB_LOCK_TIMEOUT_S: int = Field(1, ge=0, description="lock_timeout（秒，下发为毫秒）")
    DB_IDLE_IN_TX_TIMEOUT_S: int = Field(5, ge=0, description="idle_in_transaction_session_timeout（秒）")

    # ---- V2 分进程连接池 profile（默认值来自设计 §8.3 首版建议）----
    # api-admin: 后台读模型/配置发布；market-ingest: 行情热路径；execution: 下单/心跳；
    # cognition: 研究/AI；evaluation: 标签/指标/归档；replay: 回放（默认并发 2）。
    DB_API_POOL_SIZE: int = Field(5, ge=1)
    DB_API_POOL_OVERFLOW: int = Field(2, ge=0)
    DB_API_STMT_TIMEOUT_S: int = Field(2, ge=1)
    DB_MARKET_POOL_SIZE: int = Field(8, ge=1)
    DB_MARKET_POOL_OVERFLOW: int = Field(2, ge=0)
    DB_MARKET_STMT_TIMEOUT_S: int = Field(5, ge=1)
    DB_EXECUTION_POOL_SIZE: int = Field(5, ge=1)
    DB_EXECUTION_POOL_OVERFLOW: int = Field(1, ge=0)
    DB_EXECUTION_STMT_TIMEOUT_S: int = Field(5, ge=1)
    DB_COGNITION_POOL_SIZE: int = Field(3, ge=1)
    DB_COGNITION_POOL_OVERFLOW: int = Field(2, ge=0)
    DB_COGNITION_STMT_TIMEOUT_S: int = Field(5, ge=1)
    DB_EVALUATION_POOL_SIZE: int = Field(3, ge=1)
    DB_EVALUATION_POOL_OVERFLOW: int = Field(1, ge=0)
    DB_EVALUATION_STMT_TIMEOUT_S: int = Field(30, ge=1)
    DB_REPLAY_POOL_SIZE: int = Field(2, ge=1)
    DB_REPLAY_POOL_OVERFLOW: int = Field(1, ge=0)
    DB_REPLAY_STMT_TIMEOUT_S: int = Field(30, ge=1)

    # Redis
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_PASSWORD: str = ""

    # ---- V2 namespace（键前缀：pm:{schema_version}:{env}:{role}）----
    REDIS_ENV: str = "prod"
    REDIS_SCHEMA_VERSION: str = "v2"

    # ---- V2 Control Redis（fail-closed：故障禁止增仓）----
    REDIS_CONTROL_URL: str = ""
    REDIS_CONTROL_HOST: str = "localhost"
    REDIS_CONTROL_PORT: int = 6379
    REDIS_CONTROL_DB: int = 0
    REDIS_CONTROL_PASSWORD: str = ""
    REDIS_CONTROL_MAX_CONNECTIONS: int = Field(20, ge=1)
    REDIS_CONTROL_CONNECT_TIMEOUT_S: float = Field(2.0, gt=0)
    REDIS_CONTROL_READ_TIMEOUT_S: float = Field(2.0, gt=0)
    REDIS_CONTROL_HEALTH_CHECK_INTERVAL_S: float = Field(30.0, gt=0)

    # ---- V2 Cache Redis（可丢：故障只降级不改变业务判断）----
    REDIS_CACHE_URL: str = ""
    REDIS_CACHE_HOST: str = "localhost"
    REDIS_CACHE_PORT: int = 6379
    REDIS_CACHE_DB: int = 1
    REDIS_CACHE_PASSWORD: str = ""
    REDIS_CACHE_MAX_CONNECTIONS: int = Field(20, ge=1)
    REDIS_CACHE_CONNECT_TIMEOUT_S: float = Field(2.0, gt=0)
    REDIS_CACHE_READ_TIMEOUT_S: float = Field(2.0, gt=0)
    REDIS_CACHE_HEALTH_CHECK_INTERVAL_S: float = Field(30.0, gt=0)
    REDIS_CACHE_TTL_S: int = Field(300, ge=1, description="默认有限 TTL；永久 TTL 禁止")
    REDIS_CACHE_TTL_JITTER_S: int = Field(30, ge=0)
    REDIS_CACHE_BYPASS_ON_ERROR: bool = True

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

    # ---- V2 Redis endpoint 组装 ----

    @property
    def redis_namespace(self) -> str:
        """基础键前缀：pm:{schema_version}:{env}，例如 pm:v2:prod。"""
        return f"pm:{self.REDIS_SCHEMA_VERSION}:{self.REDIS_ENV}"

    @staticmethod
    def _redis_url(host: str, port: int, db: int, password: str) -> str:
        auth = f":{quote(password, safe='')}@" if password else ""
        return f"redis://{auth}{host}:{port}/{db}"

    @property
    def control_redis_endpoint(self) -> ControlRedisEndpoint:
        url = self.REDIS_CONTROL_URL or self._redis_url(
            self.REDIS_CONTROL_HOST, self.REDIS_CONTROL_PORT,
            self.REDIS_CONTROL_DB, self.REDIS_CONTROL_PASSWORD,
        )
        return ControlRedisEndpoint(
            url=url,
            max_connections=self.REDIS_CONTROL_MAX_CONNECTIONS,
            connect_timeout_s=self.REDIS_CONTROL_CONNECT_TIMEOUT_S,
            read_timeout_s=self.REDIS_CONTROL_READ_TIMEOUT_S,
            health_check_interval_s=self.REDIS_CONTROL_HEALTH_CHECK_INTERVAL_S,
            namespace=f"{self.redis_namespace}:control",
        )

    @property
    def cache_redis_endpoint(self) -> CacheRedisEndpoint:
        url = self.REDIS_CACHE_URL or self._redis_url(
            self.REDIS_CACHE_HOST, self.REDIS_CACHE_PORT,
            self.REDIS_CACHE_DB, self.REDIS_CACHE_PASSWORD,
        )
        return CacheRedisEndpoint(
            url=url,
            max_connections=self.REDIS_CACHE_MAX_CONNECTIONS,
            connect_timeout_s=self.REDIS_CACHE_CONNECT_TIMEOUT_S,
            read_timeout_s=self.REDIS_CACHE_READ_TIMEOUT_S,
            health_check_interval_s=self.REDIS_CACHE_HEALTH_CHECK_INTERVAL_S,
            namespace=f"{self.redis_namespace}:cache",
            default_ttl_s=self.REDIS_CACHE_TTL_S,
            ttl_jitter_s=self.REDIS_CACHE_TTL_JITTER_S,
            bypass_on_error=self.REDIS_CACHE_BYPASS_ON_ERROR,
        )

    # ---- V2 pool profile 访问 ----

    @property
    def pool_profile_names(self) -> tuple[str, ...]:
        """六个独立进程 profile 的固定顺序。"""
        return tuple(PROFILE_FIELDS)

    def pool_profile(self, name: str) -> PoolProfile:
        """返回指定 profile 的 typed 配置；未知 profile 立即抛错。"""
        if name not in PROFILE_FIELDS:
            raise KeyError(f"unknown DB pool profile: {name!r}")
        size_attr, overflow_attr, stmt_attr = PROFILE_FIELDS[name]
        return PoolProfile(
            name=name,
            pool_size=getattr(self, size_attr),
            max_overflow=getattr(self, overflow_attr),
            statement_timeout_s=getattr(self, stmt_attr),
            pre_ping=self.DB_POOL_PRE_PING,
        )

    def connection_budget(self, replica_counts: Mapping[str, int] | None = None) -> ConnectionBudget:
        """
        计算全局连接预算（整个部署）。

        replica_counts 缺省视为每个 profile 单实例。超出命名 profile 的键或
        负副本数立即抛错，防止静默漏算某类进程。
        """
        replicas: dict[str, int] = {name: 1 for name in PROFILE_FIELDS}
        if replica_counts:
            for name, count in replica_counts.items():
                if name not in PROFILE_FIELDS:
                    raise KeyError(f"unknown profile in replica_counts: {name!r}")
                if count < 0:
                    raise ValueError(f"replica count for {name!r} must be >= 0, got {count}")
                replicas[name] = count
        per_profile: dict[str, int] = {}
        total = 0
        for name in PROFILE_FIELDS:
            cap = self.pool_profile(name).per_instance_capacity * replicas[name]
            per_profile[name] = cap
            total += cap
        limit = self.DB_MAX_CONNECTIONS - self.DB_ADMIN_RESERVED_CONNECTIONS
        return ConnectionBudget(
            per_profile=per_profile,
            replica_counts=replicas,
            total=total,
            max_connections=self.DB_MAX_CONNECTIONS,
            reserved=self.DB_ADMIN_RESERVED_CONNECTIONS,
            limit=limit,
            remaining=limit - total,
        )

    @model_validator(mode="after")
    def _validate_connection_budget(self) -> "Settings":
        """单实例部署时全局连接预算不得超限（交叉校验，design §14）。"""
        budget = self.connection_budget()
        if not budget.is_within_limit():
            raise ValueError(
                f"global DB connection budget {budget.total} exceeds usable limit "
                f"{budget.limit} (max_connections={budget.max_connections} - "
                f"reserved={budget.reserved}). Raise DB_MAX_CONNECTIONS or lower "
                f"pool sizes; do not reuse the legacy 20+10 per-process default."
            )
        return self

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
