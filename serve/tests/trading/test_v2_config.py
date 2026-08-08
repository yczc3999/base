"""
WP-00a typed config 验收测试。

覆盖：默认值、env 覆盖、字段/预算校验、连接预算公式、策略/资本字段禁止、
.env.example 契约（含全部 V2 键、无 legacy 20+10、无真实密钥）。
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import PROFILE_FIELDS, ConnectionBudget, PoolProfile, Settings

SERVE_DIR = Path(__file__).resolve().parents[2]
ENV_EXAMPLE = SERVE_DIR / ".env.example"

# 测试需要清空的 env 键（保证默认值断言确定性）
_READ_ENV_KEYS = [
    "DATABASE_HOST", "DATABASE_PORT", "DATABASE_NAME", "DATABASE_USER",
    "DATABASE_PASSWORD", "DATABASE_SCHEMA",
    "DB_MAX_CONNECTIONS", "DB_ADMIN_RESERVED_CONNECTIONS", "DB_POOL_PRE_PING",
    "DB_POOL_TIMEOUT_S", "DB_POOL_RECYCLE_S", "DB_LOCK_TIMEOUT_S",
    "DB_IDLE_IN_TX_TIMEOUT_S",
]
for _triplet in PROFILE_FIELDS.values():
    _READ_ENV_KEYS.extend(_triplet)

# 禁止进入基础设施配置的字段（platform-design §3.1：策略/资金权限另放）
_FORBIDDEN_SUBSTRINGS = (
    "bankroll", "kelly", "strategy", "capital", "edge",
    "objective", "position", "min_predictability",
)


@pytest.fixture
def clean_env(monkeypatch):
    """清空全部相关 env，保证测试读的是默认值而非本机环境。"""
    for key in _READ_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def _settings(monkeypatch, **env):
    """构造隔离的 Settings：可注入 env，禁用 .env 文件，清空无关键。"""
    for key in _READ_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, str(value))
    return Settings(_env_file=None)


# ---------------- 默认值 ----------------

def test_default_profiles_and_values(clean_env):
    s = Settings(_env_file=None)
    assert s.pool_profile_names == (
        "api", "market", "execution", "cognition", "evaluation", "replay",
    )
    expected = {
        "api": (5, 2, 2),
        "market": (8, 2, 5),
        "execution": (5, 1, 5),
        "cognition": (3, 2, 5),
        "evaluation": (3, 1, 30),
        "replay": (2, 1, 30),
    }
    for name, (size, overflow, stmt) in expected.items():
        p = s.pool_profile(name)
        assert isinstance(p, PoolProfile)
        assert (p.pool_size, p.max_overflow, p.statement_timeout_s) == (size, overflow, stmt)


def test_application_name_and_capacity(clean_env):
    s = Settings(_env_file=None)
    assert s.pool_profile("api").application_name == "pollymarket_v2_api"
    assert s.pool_profile("market").application_name == "pollymarket_v2_market"
    assert s.pool_profile("api").per_instance_capacity == 7
    assert s.pool_profile("replay").per_instance_capacity == 3


def test_pool_globals_defaults(clean_env):
    s = Settings(_env_file=None)
    assert s.DB_MAX_CONNECTIONS == 100
    assert s.DB_ADMIN_RESERVED_CONNECTIONS == 20
    assert s.DB_POOL_PRE_PING is True
    assert s.DB_POOL_TIMEOUT_S == 3.0
    assert s.DB_POOL_RECYCLE_S == 1800
    assert s.DB_LOCK_TIMEOUT_S == 1
    assert s.DB_IDLE_IN_TX_TIMEOUT_S == 5


# ---------------- env 覆盖 ----------------

def test_env_override(monkeypatch):
    s = _settings(
        monkeypatch,
        DB_API_POOL_SIZE=9,
        DB_API_POOL_OVERFLOW=4,
        DB_API_STMT_TIMEOUT_S=6,
        DB_MAX_CONNECTIONS=200,
        DB_ADMIN_RESERVED_CONNECTIONS=30,
    )
    p = s.pool_profile("api")
    assert (p.pool_size, p.max_overflow, p.statement_timeout_s) == (9, 4, 6)
    assert s.DB_MAX_CONNECTIONS == 200
    assert s.DB_ADMIN_RESERVED_CONNECTIONS == 30


# ---------------- 校验 ----------------

def test_invalid_pool_size_rejected(monkeypatch):
    with pytest.raises(ValidationError):
        _settings(monkeypatch, DB_API_POOL_SIZE=0)


def test_invalid_overflow_rejected(monkeypatch):
    with pytest.raises(ValidationError):
        _settings(monkeypatch, DB_MARKET_POOL_OVERFLOW=-1)


def test_invalid_statement_timeout_rejected(monkeypatch):
    with pytest.raises(ValidationError):
        _settings(monkeypatch, DB_REPLAY_STMT_TIMEOUT_S=0)


def test_budget_oversubscription_rejected(monkeypatch):
    # api 单实例 100+2=102 已超 usable limit 80 → Settings() 必须拒绝
    with pytest.raises(ValidationError, match="connection budget"):
        _settings(monkeypatch, DB_API_POOL_SIZE=100)


def test_unknown_profile_rejected(clean_env):
    s = Settings(_env_file=None)
    with pytest.raises(KeyError):
        s.pool_profile("does_not_exist")


# ---------------- 连接预算 ----------------

def test_default_budget(clean_env):
    s = Settings(_env_file=None)
    b = s.connection_budget()
    assert isinstance(b, ConnectionBudget)
    assert b.per_profile == {
        "api": 7, "market": 10, "execution": 6,
        "cognition": 5, "evaluation": 4, "replay": 3,
    }
    assert b.total == 35
    assert b.limit == 80
    assert b.remaining == 45
    assert b.is_within_limit() is True


def test_budget_with_replicas(clean_env):
    s = Settings(_env_file=None)
    b = s.connection_budget(replica_counts={"market": 2})
    assert b.per_profile["market"] == 20
    assert b.total == 45
    assert b.is_within_limit() is True


def test_budget_replica_validation(clean_env):
    s = Settings(_env_file=None)
    with pytest.raises(ValueError):
        s.connection_budget(replica_counts={"market": -1})
    with pytest.raises(KeyError):
        s.connection_budget(replica_counts={"nope": 1})


# ---------------- 禁止字段守卫（验收点 9） ----------------

def test_no_strategy_capital_or_business_fields(clean_env):
    Settings(_env_file=None)  # 确保默认值可加载
    for field in Settings.model_fields:
        lower = field.lower()
        for bad in _FORBIDDEN_SUBSTRINGS:
            assert bad not in lower, f"forbidden field leaked into Settings: {field!r}"


# ---------------- .env.example 契约 ----------------

def test_env_example_contains_all_v2_keys():
    content = ENV_EXAMPLE.read_text(encoding="utf-8")
    expected = [
        f for triplet in PROFILE_FIELDS.values() for f in triplet
    ] + [
        "DB_MAX_CONNECTIONS", "DB_ADMIN_RESERVED_CONNECTIONS", "DB_POOL_PRE_PING",
        "DB_POOL_TIMEOUT_S", "DB_POOL_RECYCLE_S", "DB_LOCK_TIMEOUT_S",
        "DB_IDLE_IN_TX_TIMEOUT_S",
    ]
    for key in expected:
        assert key in content, f".env.example missing {key}"


def test_env_example_drops_legacy_20_10():
    content = ENV_EXAMPLE.read_text(encoding="utf-8")
    assert "DATABASE_POOL_SIZE" not in content
    assert "DATABASE_MAX_OVERFLOW" not in content


def test_env_example_uses_secret_ref_only():
    content = ENV_EXAMPLE.read_text(encoding="utf-8")
    assert "SECRET_REF" in content
    for forbidden in ("sk-", "pk_", "AKIA", "BEGIN .*PRIVATE KEY", "0x[0-9a-fA-F]{16}"):
        assert forbidden not in content, f".env.example contains key-like material: {forbidden}"
