# WP-07C — 常驻运行时进程装配（P-shadow qualification 运行时）

> 任务合同。实现者只执行本文档；完成后生成 manifest `wp-07c-resident-runtime.md`。

## 目标

把已建成的 trading 运行时编排类装配成**可常驻运行的进程**（supervisor 显式注册、
隔离启动、优雅关闭），打通 outbox → trading handler 的消费链，为 P-shadow
qualification 提供运行本体。

## 前置

WP-07A ACCEPTED；head=`b1000071`。业务 Logic/handler/状态机与 runtime 编排类已建
（WP-01B~06），本 WP 只装配、不改业务语义。

## Checkpoint

- **A（本批已交付）**：DB pool profile（reconciliation/outbox）+ handler↔outbox 适配
  （`_dispatch.py`）+ outbox 常驻进程（`outbox.py`）+ supervisor 骨架与显式注册
  （`supervisor.py`/`assembly.py`/`__main__.py`）+ §3.1→§8 显式映射 + 单测/集成测试。
- **B（后续）**：把 cognition/execution/evaluation/reconciliation/replay runtime 的
  build 工厂接入 `default_specs()`（需 provider 凭证、模型网关、execution vault）。

## 验收

- `python -m runtimes.trading --dry-run` 打印注册清单（不联网）
- dispatch/supervisor 单测通过（无 DB）
- outbox 集成测试真 PG 复跑（`V2_TEST_ADMIN_DATABASE_URL`）
- 全仓 `pytest` 0 fail；`compileall` OK；`git diff --check` OK

## 边界

不做真实 provider 下单（shadow-only）；不改 Logic/handler 语义；不做 WP-08；
不做 canary/live 升级。
