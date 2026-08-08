# COMPLETION MANIFEST — WP-00a · typed config 与数据库连接基础

- Work package: `WP-00` 第一子任务 `WP-00a`
- 状态: **DONE**（测试、迁移不涉及、数据约束、manifest 均通过）
- 日期: 2026-08-08
- 执行模型: DeepSeek V4 Flash
- 规范依据: `AGENTS.md` → `docs/v2/ARCHITECTURE.md` → `serve/docs/polymarket-v2-platform-design.md` → `serve/docs/performance-cache-database-design.md` → `serve/docs/v2-implementation-contract.md`
- 验收命令: 见 §3

---

## 1. 修改文件

| 文件 | 变更 | 说明 |
|---|---|---|
| `serve/app/config.py` | 修改（+175/-28） | typed 基础设施配置：六进程 pool profile、全局预算、校验、env 覆盖 |
| `serve/app/services/database.py` | 修改（+149/-…） | 分进程 engine/session factory、connect_args、lifespan dispose、legacy 兼容 |
| `serve/.env.example` | 修改（+47） | 移除 legacy 20+10，新增 V2 连接池分区与 SECRET_REF 示例 |
| `serve/requirements-dev.txt` | 修改 | 钉 pytest-asyncio 0.23.x（conftest 兼容）、补 aiosqlite |
| `serve/tests/trading/test_v2_config.py` | 新增 | 16 个 typed config 测试 |
| `serve/tests/trading/test_v2_database_profiles.py` | 新增 | 15 个分进程连接池测试 |
| `serve/docs/manifests/wp-00a-config-database.md` | 新增 | 本 manifest |

`serve/requirements.txt` 未改动——WP-00a 所需运行时依赖（pydantic / pydantic-settings / sqlalchemy[asyncio] / asyncpg）已在其中。

范围外未动：`serve/README.md`（改动前已存在的未提交文档）、`AGENTS.md`、`serve/docs/*.md`（上游设计文档）、Base 业务代码、V1 代码。

---

## 2. 实现内容

### 2.1 `app/config.py` — typed 基础设施配置

- 保留全部 Base `Settings` 字段（`APP_NAME`/`DATABASE_HOST`/`REDIS_*`/`TOKEN_*` 等），`model_config={"env_file": ".env", "extra": "ignore"}` 不变。
- **删除** `DATABASE_POOL_SIZE=20` / `DATABASE_MAX_OVERFLOW=10`（全仓仅旧 database.py 引用，重写后无引用；`extra="ignore"` 兼容残留 `.env` 键）。
- 新增 `PoolProfile`（typed）：`name / pool_size / max_overflow / statement_timeout_s / pre_ping`，含 `application_name`（`pollymarket_v2_{name}`）与 `per_instance_capacity`。
- 新增六个进程 profile（扁平 env 键，继承 Base 命名风格）：
  `api / market / execution / cognition / evaluation / replay`，默认值来自 performance 设计 §8.3 首版建议。
- 新增全局参数：`DB_MAX_CONNECTIONS=100`、`DB_ADMIN_RESERVED_CONNECTIONS=20`、`DB_POOL_PRE_PING=true`、`DB_POOL_TIMEOUT_S=3`、`DB_POOL_RECYCLE_S=1800`、`DB_LOCK_TIMEOUT_S=1`、`DB_IDLE_IN_TX_TIMEOUT_S=5`。
- 新增 `ConnectionBudget`（typed）与 `Settings.connection_budget(replica_counts)`：
  `Σ(profile_replica × (pool_size+max_overflow)) ≤ max_connections − reserved`，超限即 `ValueError`，副本数非法（未知 profile / 负数）立即抛错。
- `model_validator(mode="after")` 单实例默认预算交叉校验，超限拒绝启动。
- 字段约束：`pool_size≥1`、`max_overflow≥0`、`statement_timeout_s≥1`、`max_connections≥1`、`pool_timeout>0`、`recycle>0`。
- **禁止项**：无策略参数、无资本权限、无 secret 明文、无运行时 latest 配置、无业务逻辑（测试有缺失性守卫）。

### 2.2 `app/services/database.py` — 分进程 engine profile

- `build_connect_args(cfg, profile)`：`application_name` + `server_settings`（`statement_timeout` 按进程：API 2s / 热 worker 5s / batch-replay 30s；`lock_timeout`、`idle_in_transaction_session_timeout` 部署级，毫秒下发）。
- `build_engine(cfg, profile)`：`create_async_engine` 带 `pool_size / max_overflow / pool_pre_ping / pool_timeout / pool_recycle / connect_args`，echo 跟随 `APP_DEBUG`。
- `DatabaseEngines` 注册表：惰性建 engine，`engine() / session_factory() / connect_args() / budget() / engine_count() / dispose()`；dispose 幂等、清空注册表。
- 模块级 `engines = DatabaseEngines(settings)` 单例。
- **Legacy 兼容**：`engine`、`async_session` 退化为默认 api profile；`get_db(profile="api")` 保持原有 FastAPI 依赖语义。
- `engine_lifespan()`（asynccontextmanager）与 `dispose_engines()` 提供 lifespan dispose 原语（供 V2 runtime / main.py 后续接入）。
- **禁止项**：无业务查询、无内部 commit、无全进程统一 20+10。

### 2.3 `.env.example`

- 删除 `DATABASE_POOL_SIZE` / `DATABASE_MAX_OVERFLOW`。
- 新增 V2 连接池分区：全局 7 项 + 六 profile 18 项，每项带默认值注释。
- 密钥只演示 `SECRET_REF=vault://…/v1` 形式，注明禁止真实 key（凭据由 vault 解密，见 WP-05）。

### 2.4 `requirements-dev.txt`

- `pytest-asyncio>=0.23,<0.24`：conftest 依赖 session 级 `event_loop` fixture override，pytest-asyncio 1.x 已移除该 override。
- 新增 `aiosqlite>=0.20`：Base `tests/conftest.py` 用 SQLite 内存库替代 PostgreSQL，原 dev 依赖遗漏该包（全量套件因此无法运行）。

### 2.5 测试

- `test_v2_config.py`（16）：默认值、env 覆盖、字段/预算校验、连接预算公式、禁止字段守卫、`.env.example` 契约（含全部 V2 键、无 legacy 20+10、无真实密钥）。
- `test_v2_database_profiles.py`（15）：六进程独立 engine、惰性创建、池参数（pre_ping/timeout/recycle）、asyncpg URL、application_name、分进程 statement timeout、禁止 20+10、预算、dispose 幂等/重建、lifespan、legacy `get_db`/`async_session` 兼容。
- 全部为离线测试，不连接真实 PostgreSQL（engine 构建惰性）。

---

## 3. 命令与真实结果

环境说明：本机无系统 Python 包（连 pytest 都未安装），因此创建 `serve/.venv` 并安装 `requirements-dev.txt`。

```bash
# 0) 环境准备
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
# → sqlalchemy 2.0.51 | asyncpg 0.31.0 | pydantic 2.13.4 | pytest 9.1.1 | pytest-asyncio 0.23.8 | aiosqlite 已装

# 1) compileall
python3 -m compileall -q app tests
# → exit 0，无输出

# 2) typed config 测试
.venv/bin/pytest -q tests/trading/test_v2_config.py
# → 16 passed in 0.07s

# 3) 分进程连接池测试
.venv/bin/pytest -q tests/trading/test_v2_database_profiles.py
# → 15 passed in 0.20s

# 4) 全量回归（Base 未被破坏）
.venv/bin/pytest -q
# → 242 passed, 1 warning in 2.46s
#   （1 warning = tests/conftest.py 的 event_loop 重定义弃用警告，属既有 conftest 代码，非本次引入）

# 5) 空白/尾随检查
git diff --check
# → 无输出，exit 0
```

---

## 4. 配置与连接预算证据

### 4.1 六进程默认池（env 键 / 默认值）

| profile | pool_size | max_overflow | statement_timeout_s | application_name |
|---|---|---|---|---|
| api | 5 | 2 | 2 | `pollymarket_v2_api` |
| market | 8 | 2 | 5 | `pollymarket_v2_market` |
| execution | 5 | 1 | 5 | `pollymarket_v2_execution` |
| cognition | 3 | 2 | 5 | `pollymarket_v2_cognition` |
| evaluation | 3 | 1 | 30 | `pollymarket_v2_evaluation` |
| replay | 2 | 1 | 30 | `pollymarket_v2_replay` |

### 4.2 全局连接预算（单实例部署，可计算）

```
Σ(profile_replica × (pool_size + max_overflow))
= api 7 + market 10 + execution 6 + cognition 5 + evaluation 4 + replay 3
= 35
limit = DB_MAX_CONNECTIONS(100) − DB_ADMIN_RESERVED_CONNECTIONS(20) = 80
remaining = 80 − 35 = 45   →  35 ≤ 80 ✓（默认值通过交叉校验）
```

- 副本缩放：`connection_budget({"market": 2})` → market=20，total=45，仍 ≤80 ✓。
- 超限拒绝：`DB_API_POOL_SIZE=100` → total=130 > 80 → `Settings()` 抛 `ValidationError`（"connection budget … exceeds usable limit"）。
- 非法副本数：负值 → `ValueError`；未知 profile → `KeyError`。

### 4.3 引擎参数（SQLAlchemy 2.0.51 实测自省）

```
api pool: maxsize=5, max_overflow=2, pre_ping=True, timeout=3.0s, recycle=1800s
connect_args["application_name"] = "pollymarket_v2_api"
connect_args["server_settings"] = {
  "statement_timeout": "2000",                      # api；market/execution/cognition=5000；evaluation/replay=30000
  "lock_timeout": "1000",
  "idle_in_transaction_session_timeout": "5000",
}
engine.url: backend=postgresql, driver=asyncpg
```

---

## 5. 未解决 blocker

无阻塞型 blocker。

非阻塞说明：
- `engine_lifespan()` / `dispose_engines()` 已实现，但 `app/main.py`（范围外）尚未调用；V2 runtime 进程接入时启用。
- 六进程集合含 `replay`，未含 `reconciliation`（performance 设计 §3 中的第七类进程）；后续 WP 如需，在 `config.PROFILE_FIELDS` 与 `.env.example` 追加即可，预算公式自动纳入。
- 环境依赖：本机原无任何 Python 包，已建 `serve/.venv`（已在 `.gitignore`）。`serve/README.md`、`AGENTS.md`、`serve/docs/*.md` 为改动前既有的未提交文档，本次未触碰。

---

## 6. 回滚方式

```bash
# 若已提交：回退到上一提交
git checkout -- serve/app/config.py serve/app/services/database.py serve/.env.example serve/requirements-dev.txt
git rm -rf serve/tests/trading serve/docs/manifests/wp-00a-config-database.md   # 若需连同测试/manifest 一起回退
```

- 本次不触碰数据库 schema（无迁移）、无真实密钥、无生产数据，回滚无副作用。
- `serve/requirements-dev.txt` 回滚会恢复 `pytest-asyncio>=0.23` 原样；如保留新钉版本亦无功能影响。
- 唯一行为变化：Base `get_db`/`async_session` 底层池从 legacy 20+10 变为 api 5+2——若 legacy 并发峰值实测需要更大池，通过 `DB_API_POOL_SIZE/DB_API_POOL_OVERFLOW` env 上调并同步调大 `DB_MAX_CONNECTIONS` 即可，无需改代码。

---

## 7. Completion manifest 路径及 SHA-256

- 路径: `serve/docs/manifests/wp-00a-config-database.md`
- SHA-256（口径：对本文件**删除"恰好为 64 位十六进制"的哈希行**后的内容计算，避免哈希行自引用；该口径与存储值无关，重写哈希值不影响结果）：

```text
1834d4fcf93192b24ee9e684c1c5a63bbd181ed79321eb5cdbe24e2d7db55663
```

验证方式：

```bash
sed -e '/^[0-9a-f]\{64\}$/d' serve/docs/manifests/wp-00a-config-database.md | sha256sum
```
