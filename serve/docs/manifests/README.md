# Polymarket V2 — 工作包完成索引（审查入口）

> 每个 `WP-*` 子任务完成后必须在此登记，并附独立 completion manifest。
> manifest 模板见 `serve/docs/v2-implementation-contract.md` §13。

## 当前状态

> **WP-00 总状态：IN PROGRESS**（00a/00b 完成，00c/00d 未开始）

| WP | 子任务 | 状态 | Manifest | SHA-256（去除哈希行口径） | 完成日期 |
|---|---|---|---|---|---|
| WP-00 | 00a typed config 与数据库连接基础 | ✅ DONE | `wp-00a-config-database.md` | `1834d4fcf93192b24ee9e684c1c5a63bbd181ed79321eb5cdbe24e2d7db55663` | 2026-08-08 |
| WP-00 | 00b Control Redis / Cache Redis | ✅ DONE | `wp-00b-redis.md` | `fd1ff118254c440deb1f62c33dec5542e7e130c743d4f0106a96c258211775ef` | 2026-08-08 |
| WP-00 | 00c Artifact Store | ⏳ pending | — | — | — |
| WP-00 | 00d Observability / lifespan | ⏳ pending | — | — | — |
| WP-01A | 0001/0002 迁移、control/artifact/outbox Models、UoW/Outbox | ⏳ pending | — | — | — |
| WP-01B | 0010/0011 Gamma/CLOB schema/Driver、universe/book ingest | ⏳ pending | — | — | — |
| WP-01C | 0012/0013 contract/component/cohort/screening | ⏳ pending | — | — | — |
| WP-02 | 0020/0021 AI invocation/model gateway/evidence/forecast | ⏳ pending | — | — | — |
| WP-03 | 0030/0031 decision/portfolio/shadow execution/ledger | ⏳ pending | — | — | — |
| WP-04 | 0040/0041 label/evaluation/replay/promotion/read projections | ⏳ pending | — | — | — |
| WP-05 | 0050/0051 vault/account/private CLOB/User WS/reconcile | ⏳ pending | — | — | — |
| WP-06 | 0052 Polygon/relayer/settlement | ⏳ pending | — | — | — |
| WP-07A | Admin API + frontend types/query scaffolding | ⏳ pending | — | — | — |
| WP-07B | 14 菜单页、5 详情页与交互 | ⏳ pending | — | — | — |
| WP-08 | 0090 分区/归档/perf harness/alerts/soak | ⏳ pending | — | — | — |

## 审查方式

1. 打开目标 WP 的 manifest，核对：修改文件（§1）、实现内容（§2）、命令与真实结果（§3）、配置/预算证据（§4）、未解决 blocker（§5）、回滚方式（§6）、manifest 路径 + SHA（§7）。
2. 复验命令见各 manifest §3；全量验收 = 编译 + 目标测试 + `git diff --check` + 全量回归。
3. SHA-256 口径：对 manifest **删除"恰好为 64 位十六进制"的哈希行**后的内容计算，与存储值无关、可复现。

```bash
# 复验某个 manifest 的哈希
sed -e '/^[0-9a-f]\{64\}$/d' serve/docs/manifests/<manifest>.md | sha256sum
```

## 依赖链

`WP-00` → `WP-01A` → `WP-01B` → `WP-01C` → `WP-02` → `WP-03` → `WP-04` → `WP-05` → `WP-06`；`WP-07A/07B` 依赖各域对应 WP；`WP-08` 依赖全部。
