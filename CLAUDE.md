# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

本目录是 **Polymarket V2 深研交易引擎**（活跃项目），不是通用 Base 平台。此前本文件是 `base/` 模板的拷贝（CRUD/RBAC/Worker 说明），与真实引擎不符，已废止。

## 权威文档（按序读，勿从本文件猜业务）

1. `AGENTS.md` — 本仓编码规则、架构分层、构建/验证命令（**离代码最近的权威**）
2. `serve/docs/tasks/README.md` — **当前任务单一入口**（WP 交付协议；实现者只执行它指向的任务文档）
3. `serve/docs/manifests/README.md` — WP 完成索引（审查入口）
4. `serve/docs/` 设计文档：`polymarket-v2-platform-design.md`、`polymarket-integration-design.md`、`ai-observability-replay-design.md`、`performance-cache-database-design.md`、`v2-implementation-contract.md`
5. `/code/pollymarket/docs/v2/ARCHITECTURE.md` — V2 唯一施工规范（位于本仓外）

通用 Base 能力（`init.sql`、`serve/databases/migrations/`、BaseLogic/crud_router/Worker 等）仅在处理通用后台基建时参考，不作为 V2 交易引擎的业务权威。工作区总览见 `/code/pollymarket/CLAUDE.md`。
