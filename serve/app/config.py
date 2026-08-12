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
from urllib.parse import quote, quote_plus, urlsplit

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
    "reconciliation": ("DB_RECONCILIATION_POOL_SIZE", "DB_RECONCILIATION_POOL_OVERFLOW", "DB_RECONCILIATION_STMT_TIMEOUT_S"),
    "outbox": ("DB_OUTBOX_POOL_SIZE", "DB_OUTBOX_POOL_OVERFLOW", "DB_OUTBOX_STMT_TIMEOUT_S"),
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

    # ---- V2 分进程连接池 profile ----
    # api-admin: 后台读模型/配置发布；market-ingest: 行情热路径；execution: 下单/心跳；
    # cognition: 研究/AI；evaluation: 标签/指标/归档；replay: 回放（默认并发 2）；
    # reconciliation: 订单/账本/链对账；outbox: 发布/清扫/消费（WP-07C 常驻运行时装配）。
    # WP-07A Admin read-plane 的 32 并发门要求 api pool 可同时承载 32 个
    # request UoW；24 个常驻 + 8 个 overflow，在默认全局预算中仍保留 20 个连接。
    DB_API_POOL_SIZE: int = Field(24, ge=1)
    DB_API_POOL_OVERFLOW: int = Field(8, ge=0)
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
    DB_RECONCILIATION_POOL_SIZE: int = Field(2, ge=1)
    DB_RECONCILIATION_POOL_OVERFLOW: int = Field(1, ge=0)
    DB_RECONCILIATION_STMT_TIMEOUT_S: int = Field(30, ge=1)
    DB_OUTBOX_POOL_SIZE: int = Field(2, ge=1)
    DB_OUTBOX_POOL_OVERFLOW: int = Field(1, ge=0)
    DB_OUTBOX_STMT_TIMEOUT_S: int = Field(30, ge=1)

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

    # ---- V2 Artifact Store（performance 设计 §6.2 / §14）----
    ARTIFACT_DRIVER: str = "local"
    ARTIFACT_LOCAL_ROOT: str = "./storage/v2-artifacts"
    ARTIFACT_INLINE_THRESHOLD_BYTES: int = Field(16384, ge=0)
    ARTIFACT_COMPRESSION_THRESHOLD_BYTES: int = Field(16384, ge=0)
    ARTIFACT_ZSTD_LEVEL: int = Field(6, ge=1)
    ARTIFACT_MAX_OBJECT_BYTES: int = Field(67_108_864, ge=1)
    ARTIFACT_VERIFY_ON_READ: bool = True

    # ---- V2 Artifact S3 Driver（WP-00c2；凭据走标准 provider chain，无 key 字段）----
    ARTIFACT_S3_BUCKET: str = ""
    ARTIFACT_S3_PREFIX: str = "v2-artifacts"
    ARTIFACT_S3_REGION: str = "us-east-1"
    ARTIFACT_S3_ENDPOINT_URL: str = ""
    ARTIFACT_S3_ADDRESSING_STYLE: str = "auto"
    ARTIFACT_S3_CONNECT_TIMEOUT_S: float = Field(2.0, gt=0)
    ARTIFACT_S3_READ_TIMEOUT_S: float = Field(10.0, gt=0)
    ARTIFACT_S3_MAX_POOL_CONNECTIONS: int = Field(20, ge=1)
    ARTIFACT_S3_MAX_ATTEMPTS: int = Field(3, ge=1)
    ARTIFACT_S3_EXPECTED_BUCKET_OWNER: str = ""
    ARTIFACT_S3_ALLOW_INSECURE_HTTP: bool = False

    # ---- V2 技术可观测性（WP-00d1；performance 设计 §12 / platform 设计 §4）----
    OBS_LOG_LEVEL: str = "INFO"
    OBS_LOG_JSON: bool = True
    OBS_SERVICE_NAME: str = "pollymarket-v2"
    OBS_SERVICE_VERSION: str = "dev"
    PROMETHEUS_ENABLED: bool = True
    OTEL_ENABLED: bool = False
    OTEL_EXPORTER_OTLP_ENDPOINT: str = ""
    OTEL_ALLOW_INSECURE_HTTP: bool = False
    OTEL_TRACE_SAMPLE_RATIO: float = Field(0.05, ge=0.0, le=1.0)
    OTEL_EXPORT_TIMEOUT_S: float = Field(5.0, gt=0)

    # ---- V2 Runtime（WP-00d2；health 探测超时，秒，>0）----
    RUNTIME_HEALTH_TIMEOUT_S: float = Field(2.0, gt=0)

    # ---- V2 Vault（WP-05；只保存 keyring/secret 引用，绝不保存 key/secret 明文）----
    # keyring 引用形如 "env://PM_V2_VAULT_MASTER_KEY" / "file:///run/secrets/pm-v2-keyring" /
    # KMS ARN；master key bytes 由调用方从该引用加载，不入 DB、不入 env 明文。
    PM_V2_VAULT_KEYRING_REF: str = ""
    # AAD 绑定的环境名（dev/staging/prod）；随 config 版本冻结。
    PM_V2_VAULT_ENV: str = "dev"
    # 执行 egress 模式：shadow（fake transport，WP-05 唯一合法值）；真实 provider 激活阻塞。
    PM_V2_EXECUTION_EGRESS_MODE: str = "shadow"

    # ---- V2 pipeline driver（WP-07C Checkpoint B）----
    # AI 推理段（G1/G2/G4-G7）门控：默认关（不烧 AI 计费）；放行后接生产模型网关。
    PM_V2_PIPELINE_AI_ENABLED: bool = False

    # ---- V2 Provider endpoint / timeout（WP-05；基础设施 typed 配置）----
    # 真实 provider 基址留空（fake-only）；激活前必须由部署显式注入。
    PM_V2_PROVIDER_BASE_URL: str = ""
    PM_V2_PROVIDER_CONNECT_TIMEOUT_S: float = Field(2.0, gt=0)
    PM_V2_PROVIDER_READ_TIMEOUT_S: float = Field(10.0, gt=0)
    PM_V2_PROVIDER_MAX_ATTEMPTS: int = Field(3, ge=1)
    # /v1/heartbeats 调度间隔（秒）；使用 monotonic clock，漂移≤500ms。
    PM_V2_HEARTBEAT_INTERVAL_S: float = Field(5.0, gt=0)
    PM_V2_HEARTBEAT_DRIFT_MS: float = Field(500.0, ge=0)

    # ---- V2 Polygon / Relayer（WP-06；基础设施 typed 配置，fake-only）----
    # 真实 RPC/Relayer 端点留空（fake-only）；激活前必须由部署显式注入。
    PM_V2_POLYGON_RPC_URLS: str = ""       # 逗号分隔（≥3 个 finalized 一致性节点）
    PM_V2_POLYGON_CHAIN_ID: int = Field(137, ge=1)
    PM_V2_POLYGON_FINALIZED_TAG: str = "finalized"
    PM_V2_RELAYER_BASE_URL: str = ""
    PM_V2_RELAYER_NONCE_TIMEOUT_S: float = Field(5.0, gt=0)
    PM_V2_RELAYER_SUBMIT_TIMEOUT_S: float = Field(15.0, gt=0)
    PM_V2_RELAYER_STATUS_TIMEOUT_S: float = Field(5.0, gt=0)
    PM_V2_RELAYER_DEADLINE_TTL_S: int = Field(600, ge=1)
    # registry 复核：启动与每次 chain operation 前必须 exact 匹配的 registry version。
    PM_V2_CONTRACT_REGISTRY_VERSION: str = "polygon-mainnet-v1"
    PM_V2_CONTRACT_REGISTRY_SNAPSHOT_BLOCK: int = Field(91842167, gt=0)
    # 交易最大重试/未知恢复上界（fake-only：仅用于测试容量，不用于真实重发）。
    PM_V2_CHAIN_RECOVERY_LIMIT: int = Field(200, ge=1)

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
        """八个独立进程 profile 的固定顺序（WP-07C 增 reconciliation/outbox）。"""
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

    @model_validator(mode="after")
    def _validate_artifact_thresholds(self) -> "Settings":
        """Artifact 阈值交叉校验：0 <= inline <= compression <= max（design §8）。"""
        if not (0 <= self.ARTIFACT_INLINE_THRESHOLD_BYTES <= self.ARTIFACT_COMPRESSION_THRESHOLD_BYTES):
            raise ValueError(
                "artifact thresholds must satisfy "
                "0 <= ARTIFACT_INLINE_THRESHOLD_BYTES <= ARTIFACT_COMPRESSION_THRESHOLD_BYTES, "
                f"got inline={self.ARTIFACT_INLINE_THRESHOLD_BYTES} "
                f"compression={self.ARTIFACT_COMPRESSION_THRESHOLD_BYTES}"
            )
        if not (self.ARTIFACT_COMPRESSION_THRESHOLD_BYTES <= self.ARTIFACT_MAX_OBJECT_BYTES):
            raise ValueError(
                "artifact thresholds must satisfy compression_threshold <= max_object_bytes, "
                f"got compression={self.ARTIFACT_COMPRESSION_THRESHOLD_BYTES} "
                f"max={self.ARTIFACT_MAX_OBJECT_BYTES}"
            )
        if self.ARTIFACT_DRIVER not in ("local", "s3"):
            raise ValueError(f"ARTIFACT_DRIVER must be 'local' or 's3', got {self.ARTIFACT_DRIVER!r}")
        return self

    # ---- S3 Driver 静态校验（WP-00c2 §4）----

    @staticmethod
    def _validate_s3_prefix(prefix: str) -> None:
        """prefix 可空；非空时禁止首尾 `/`、空段、`.`、`..`、反斜杠、NUL/CR/LF。"""
        if not prefix:
            return
        if prefix.startswith("/") or prefix.endswith("/"):
            raise ValueError(
                f"ARTIFACT_S3_PREFIX must not start or end with '/': {prefix!r}"
            )
        if any(c in prefix for c in ("\x00", "\r", "\n", "\\")):
            raise ValueError(
                f"ARTIFACT_S3_PREFIX contains forbidden chars (NUL/CR/LF/backslash): {prefix!r}"
            )
        for seg in prefix.split("/"):
            if not seg or seg in (".", ".."):
                raise ValueError(
                    f"ARTIFACT_S3_PREFIX has invalid segment {seg!r}: {prefix!r}"
                )

    @staticmethod
    def _validate_s3_endpoint(url: str) -> str:
        """endpoint 必须是可解析的严格 http(s) origin。输入不得含首尾/内部 whitespace、
        ASCII control、`?`、`#`、`@`；不能依赖解析后空字符串的 truthiness 判断分隔符。
        scheme 精确 http|https、hostname 非空、path 仅空或 `/`；必须访问并验证 parsed
        port：非数字、空 port、0 或 >65535 立即 ValueError，不延迟到 boto client。
        不 normalize、不静默删字符；原 endpoint 仍原样交给 boto3。返回 scheme。"""
        if not isinstance(url, str) or not url:
            raise ValueError(
                f"ARTIFACT_S3_ENDPOINT_URL must be a non-empty absolute http(s) URL, "
                f"got {url!r}"
            )
        if any(ch.isspace() for ch in url):
            raise ValueError(
                f"ARTIFACT_S3_ENDPOINT_URL must not contain whitespace: {url!r}"
            )
        if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in url):
            raise ValueError(
                f"ARTIFACT_S3_ENDPOINT_URL contains control characters: {url!r}"
            )
        if "?" in url or "#" in url:
            raise ValueError(
                f"ARTIFACT_S3_ENDPOINT_URL must not contain query or fragment: {url!r}"
            )
        if "@" in url:
            raise ValueError(
                f"ARTIFACT_S3_ENDPOINT_URL must not contain userinfo: {url!r}"
            )
        try:
            p = urlsplit(url)
        except ValueError:
            raise ValueError(
                f"ARTIFACT_S3_ENDPOINT_URL is not parseable: {url!r}"
            ) from None
        if p.scheme not in ("http", "https"):
            raise ValueError(
                f"ARTIFACT_S3_ENDPOINT_URL scheme must be http(s), got {url!r}"
            )
        if not p.hostname:
            raise ValueError(f"ARTIFACT_S3_ENDPOINT_URL missing host: {url!r}")
        if p.path not in ("", "/"):
            raise ValueError(
                f"ARTIFACT_S3_ENDPOINT_URL must be an origin without path: {url!r}"
            )
        try:
            port = p.port
        except ValueError:
            raise ValueError(
                f"ARTIFACT_S3_ENDPOINT_URL has invalid port: {url!r}"
            ) from None
        if port is not None:
            if port <= 0 or port > 65535:
                raise ValueError(
                    f"ARTIFACT_S3_ENDPOINT_URL port out of range 1..65535: {url!r}"
                )
        else:
            # 空端口：netloc 以 ':' 结尾（如 host: / [::1]:），拒绝空 port
            if p.netloc.endswith(":"):
                raise ValueError(
                    f"ARTIFACT_S3_ENDPOINT_URL has empty port: {url!r}"
                )
        return p.scheme

    @model_validator(mode="after")
    def _validate_s3_config(self) -> "Settings":
        """S3 Driver 交叉校验（WP-00c2 §4）。"""
        if self.ARTIFACT_DRIVER == "s3":
            if not self.ARTIFACT_S3_BUCKET:
                raise ValueError(
                    "ARTIFACT_S3_BUCKET must be non-empty when ARTIFACT_DRIVER=s3"
                )
            if not self.ARTIFACT_S3_REGION:
                raise ValueError(
                    "ARTIFACT_S3_REGION must be non-empty when ARTIFACT_DRIVER=s3"
                )
        if self.ARTIFACT_S3_ADDRESSING_STYLE not in ("auto", "virtual", "path"):
            raise ValueError(
                "ARTIFACT_S3_ADDRESSING_STYLE must be one of auto|virtual|path, "
                f"got {self.ARTIFACT_S3_ADDRESSING_STYLE!r}"
            )
        if self.ARTIFACT_S3_CONNECT_TIMEOUT_S <= 0:
            raise ValueError(
                f"ARTIFACT_S3_CONNECT_TIMEOUT_S must be > 0, "
                f"got {self.ARTIFACT_S3_CONNECT_TIMEOUT_S}"
            )
        if self.ARTIFACT_S3_READ_TIMEOUT_S <= 0:
            raise ValueError(
                f"ARTIFACT_S3_READ_TIMEOUT_S must be > 0, "
                f"got {self.ARTIFACT_S3_READ_TIMEOUT_S}"
            )
        if self.ARTIFACT_S3_MAX_POOL_CONNECTIONS < 1:
            raise ValueError(
                f"ARTIFACT_S3_MAX_POOL_CONNECTIONS must be >= 1, "
                f"got {self.ARTIFACT_S3_MAX_POOL_CONNECTIONS}"
            )
        if self.ARTIFACT_S3_MAX_ATTEMPTS < 1:
            raise ValueError(
                f"ARTIFACT_S3_MAX_ATTEMPTS must be >= 1, got {self.ARTIFACT_S3_MAX_ATTEMPTS}"
            )
        self._validate_s3_prefix(self.ARTIFACT_S3_PREFIX)
        if self.ARTIFACT_S3_ENDPOINT_URL:
            scheme = self._validate_s3_endpoint(self.ARTIFACT_S3_ENDPOINT_URL)
            if scheme == "http" and not self.ARTIFACT_S3_ALLOW_INSECURE_HTTP:
                raise ValueError(
                    "ARTIFACT_S3_ENDPOINT_URL uses http:// but "
                    "ARTIFACT_S3_ALLOW_INSECURE_HTTP=false"
                )
        return self

    # ---- OTel OTLP endpoint 校验（WP-00d1）----

    @staticmethod
    def _validate_otel_endpoint(url: str) -> str:
        """OTel OTLP endpoint：绝对 http(s) URL；禁 userinfo/query/fragment/control/
        whitespace；path 仅允许空、`/` 或 `/v1/traces`（原样保留，不 normalize）。
        必须访问并验证 parsed port；返回 scheme。启用 OTel 时必填（由调用方保证）。"""
        if not isinstance(url, str) or not url:
            raise ValueError(
                f"OTEL_EXPORTER_OTLP_ENDPOINT must be a non-empty absolute http(s) URL, "
                f"got {url!r}"
            )
        if any(ch.isspace() for ch in url):
            raise ValueError(
                f"OTEL_EXPORTER_OTLP_ENDPOINT must not contain whitespace: {url!r}"
            )
        if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in url):
            raise ValueError(
                f"OTEL_EXPORTER_OTLP_ENDPOINT contains control characters: {url!r}"
            )
        if "?" in url or "#" in url:
            raise ValueError(
                f"OTEL_EXPORTER_OTLP_ENDPOINT must not contain query or fragment: {url!r}"
            )
        if "@" in url:
            raise ValueError(
                f"OTEL_EXPORTER_OTLP_ENDPOINT must not contain userinfo: {url!r}"
            )
        try:
            p = urlsplit(url)
        except ValueError:
            raise ValueError(
                f"OTEL_EXPORTER_OTLP_ENDPOINT is not parseable: {url!r}"
            ) from None
        if p.scheme not in ("http", "https"):
            raise ValueError(
                f"OTEL_EXPORTER_OTLP_ENDPOINT scheme must be http(s), got {url!r}"
            )
        if not p.hostname:
            raise ValueError(f"OTEL_EXPORTER_OTLP_ENDPOINT missing host: {url!r}")
        if p.path not in ("", "/", "/v1/traces"):
            raise ValueError(
                f"OTEL_EXPORTER_OTLP_ENDPOINT path must be empty, '/', or '/v1/traces': "
                f"{url!r}"
            )
        try:
            port = p.port
        except ValueError:
            raise ValueError(
                f"OTEL_EXPORTER_OTLP_ENDPOINT has invalid port: {url!r}"
            ) from None
        if port is not None:
            if port <= 0 or port > 65535:
                raise ValueError(
                    f"OTEL_EXPORTER_OTLP_ENDPOINT port out of range 1..65535: {url!r}"
                )
        else:
            if p.netloc.endswith(":"):
                raise ValueError(
                    f"OTEL_EXPORTER_OTLP_ENDPOINT has empty port: {url!r}"
                )
        return p.scheme

    @model_validator(mode="after")
    def _validate_observability(self) -> "Settings":
        """可观测性交叉校验：level 枚举、service name/version 格式、OTLP endpoint 严格
        origin（启用时必填、http 需 opt-in）、sample ratio 与 export timeout 边界。"""
        if self.OBS_LOG_LEVEL not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
            raise ValueError(
                f"OBS_LOG_LEVEL must be one of DEBUG/INFO/WARNING/ERROR/CRITICAL, "
                f"got {self.OBS_LOG_LEVEL!r}"
            )
        import re as _re

        if not (1 <= len(self.OBS_SERVICE_NAME) <= 64) or not _re.fullmatch(
            r"[a-zA-Z0-9._-]+", self.OBS_SERVICE_NAME
        ):
            raise ValueError(
                f"OBS_SERVICE_NAME must be 1-64 chars of [a-zA-Z0-9._-], "
                f"got {self.OBS_SERVICE_NAME!r}"
            )
        if not (1 <= len(self.OBS_SERVICE_VERSION) <= 64) or any(
            ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F
            for ch in self.OBS_SERVICE_VERSION
        ):
            raise ValueError(
                f"OBS_SERVICE_VERSION must be 1-64 chars without control/whitespace, "
                f"got {self.OBS_SERVICE_VERSION!r}"
            )
        if self.OTEL_ENABLED:
            if not self.OTEL_EXPORTER_OTLP_ENDPOINT:
                raise ValueError(
                    "OTEL_EXPORTER_OTLP_ENDPOINT must be set when OTEL_ENABLED=true"
                )
            scheme = self._validate_otel_endpoint(self.OTEL_EXPORTER_OTLP_ENDPOINT)
            if scheme == "http" and not self.OTEL_ALLOW_INSECURE_HTTP:
                raise ValueError(
                    "OTEL_EXPORTER_OTLP_ENDPOINT uses http:// but "
                    "OTEL_ALLOW_INSECURE_HTTP=false"
                )
        elif self.OTEL_EXPORTER_OTLP_ENDPOINT:
            # 未启用时即使填了 endpoint 也做格式校验，fail-fast 而非延迟到 boto/OTel
            self._validate_otel_endpoint(self.OTEL_EXPORTER_OTLP_ENDPOINT)
        if not (0.0 <= self.OTEL_TRACE_SAMPLE_RATIO <= 1.0):
            raise ValueError(
                f"OTEL_TRACE_SAMPLE_RATIO must be in [0.0, 1.0], "
                f"got {self.OTEL_TRACE_SAMPLE_RATIO}"
            )
        if self.OTEL_EXPORT_TIMEOUT_S <= 0:
            raise ValueError(
                f"OTEL_EXPORT_TIMEOUT_S must be > 0, got {self.OTEL_EXPORT_TIMEOUT_S}"
            )
        if self.RUNTIME_HEALTH_TIMEOUT_S <= 0:
            raise ValueError(
                f"RUNTIME_HEALTH_TIMEOUT_S must be > 0, got {self.RUNTIME_HEALTH_TIMEOUT_S}"
            )
        return self

    @model_validator(mode="after")
    def _validate_chain_egress(self) -> "Settings":
        """WP-06 fake-only 门：真实 Polygon RPC / Relayer URL 留空；激活前必须显式注入。

        提供非空 URL 仅用于格式校验（fail-fast），不改变 fake-only 语义：运行时 Driver
        默认 ``require_injected_transport=true``，缺 transport 即 ``wire_egress_tripwire``。
        """
        for name, value in (
            ("PM_V2_POLYGON_RPC_URLS", self.PM_V2_POLYGON_RPC_URLS),
            ("PM_V2_RELAYER_BASE_URL", self.PM_V2_RELAYER_BASE_URL),
        ):
            if not value:
                continue
            for part in value.split(","):
                part = part.strip()
                if not part:
                    continue
                if not part.startswith(("http://", "https://")):
                    raise ValueError(
                        f"{name} must be an absolute http(s) URL, got {part!r}"
                    )
        if self.PM_V2_POLYGON_CHAIN_ID != 137:
            raise ValueError(
                "PM_V2_POLYGON_CHAIN_ID must be 137 in WP-06 (only Polygon PoS supported), "
                f"got {self.PM_V2_POLYGON_CHAIN_ID}"
            )
        if self.PM_V2_POLYGON_FINALIZED_TAG != "finalized":
            raise ValueError(
                f"PM_V2_POLYGON_FINALIZED_TAG must be 'finalized', "
                f"got {self.PM_V2_POLYGON_FINALIZED_TAG!r}"
            )
        return self

    @model_validator(mode="after")
    def _validate_execution_egress(self) -> "Settings":
        """WP-05 fake-only 门：egress mode 只允许 shadow；heartbeat 漂移上界非负。"""
        if self.PM_V2_EXECUTION_EGRESS_MODE not in ("shadow",):
            raise ValueError(
                "PM_V2_EXECUTION_EGRESS_MODE must be 'shadow' in WP-05 "
                f"(real provider activation is blocked), got {self.PM_V2_EXECUTION_EGRESS_MODE!r}"
            )
        if self.PM_V2_VAULT_ENV not in ("dev", "staging", "prod"):
            raise ValueError(
                f"PM_V2_VAULT_ENV must be one of dev/staging/prod, got {self.PM_V2_VAULT_ENV!r}"
            )
        if self.PM_V2_HEARTBEAT_DRIFT_MS < 0:
            raise ValueError(
                f"PM_V2_HEARTBEAT_DRIFT_MS must be >= 0, got {self.PM_V2_HEARTBEAT_DRIFT_MS}"
            )
        return self

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
