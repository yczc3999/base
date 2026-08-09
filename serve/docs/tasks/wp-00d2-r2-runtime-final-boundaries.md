# WP-00d2-r2 — Runtime 终态竞态与 Tracing 清理边界

> 状态：**ACCEPTED**（审查者直接整改）。完成 manifest：
> `serve/docs/manifests/wp-00d2-r2-runtime-final-boundaries.md`。
> 最后更新：2026-08-09 05:32 EDT。

## 1. 目标与价值

关闭 `WP-00d2-r1` 交付后独立复验发现的四个 P1 边界，保证启动失败、关闭失败、
health/close 并发及非法 probe 返回值都不会伪造 ready 或留下旧 runtime。

## 2. 确认决策与范围

- `configure_tracing` 必须在 lifespan `try/finally` 内；初始化抛错也执行 legacy Redis/
  tracing 清理并清空 `app.state.trading_runtime`。
- `shutdown_tracing` 失败只记安全 reason code，不覆盖 body 原异常，不阻止清指针。
- close 发生后，在途 health probe 必须丢弃结果，不得把终态重写为 ready。
- DB probe 只接受 `None`；Redis probe 只接受 `dict` 且 `ok is True`；Artifact probe 只接受
  `ok is True`。其他 truthy/异形结果 fail-closed。
- 测试 monkeypatch 不得用 async 函数代替 sync 初始化器而制造 `RuntimeWarning`。

允许文件：`serve/app/main.py`、`serve/app/services/runtime.py`、
`serve/tests/trading/test_v2_runtime_lifecycle.py`、
`serve/tests/trading/test_v2_router_registration.py` 及本 task/manifest/索引。

## 3. 验收证据

- 7 artifact factory + 35 runtime + 19 router = 61 定向测试通过。
- `tests/trading` 669 通过；全量 880 通过、9 个既有弃用告警，无新 `RuntimeWarning`。
- `compileall`、`orjson 3.11.9`、`git diff --check` 通过。
- 独立审查确认当前范围无剩余 P0/P1。

## 4. 非目标、风险与回滚

不改 observability primitives、Artifact driver、Redis/DB 客户端、业务表、策略、下单或
Admin UI。回滚本任务的四个生产/测试文件差异即可；无迁移、网络或业务数据副作用。
