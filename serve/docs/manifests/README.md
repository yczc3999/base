# Polymarket V2 — 工作包完成索引（审查入口）

> 每个 `WP-*` 子任务完成后必须在此登记，并附独立 completion manifest。
> manifest 模板见 `serve/docs/v2-implementation-contract.md` §13。

## 当前状态

> **WP-00 总状态：IN PROGRESS**（00a/00b/00c1/00c2/00d1 已接受；当前 WP-00d2 READY）

| WP | 子任务 | 状态 | Manifest | SHA-256（去除哈希行口径） | 完成日期 |
|---|---|---|---|---|---|
| WP-00 | 00a typed config 与数据库连接基础 | ✅ DONE | `wp-00a-config-database.md` | `1834d4fcf93192b24ee9e684c1c5a63bbd181ed79321eb5cdbe24e2d7db55663` | 2026-08-08 |
| WP-00 | 00b Control Redis / Cache Redis | ⚠ DONE，审查要求 R1 | `wp-00b-redis.md` | `fd1ff118254c440deb1f62c33dec5542e7e130c743d4f0106a96c258211775ef` | 2026-08-08 |
| WP-00 | 00b-r1 Redis 基础不变量整改 | ⚠ DONE，审查要求 R2 | `wp-00b-r1-redis-remediation.md` | `bdb27c6de1079ea6a8383c01b495b79072d9d91588f72c1b87a00cdfd469cf32` | 2026-08-08 |
| WP-00 | 00b-r2 Redis identity 与测试稳定性整改 | ✅ DONE，审查通过 | `wp-00b-r2-redis-identity-test-stability.md` | `8f406ad4cee49fbc81383858613c4ade25c0afeaf2584b31194f5706c46b02a6` | 2026-08-08 |
| WP-00 | 00c1 Local Artifact Store | ⚠ DONE，审查要求 R1 | `wp-00c1-artifact-local.md` | `be062516eac9a64a888f3e30852e760508b68e4268872dc77f4bd6bc2123df19` | 2026-08-08 |
| WP-00 | 00c1-r1 Artifact 正确性整改 | ⚠ DONE，审查要求 R2 | `wp-00c1-r1-artifact-correctness.md` | `119e0710e20468e3c7b5a18a915fad50eac3831ce4b2179007091dd0f339cc85` | 2026-08-08 |
| WP-00 | 00c1-r2 Artifact 最终边界整改 | ✅ DONE，审查通过 | `wp-00c1-r2-artifact-final-boundaries.md` | `40cfd5f7be09ff368ecad9737f5febaccde96a2b3314309cc6d30b0c379b3220` | 2026-08-08 |
| WP-00 | 00c2 S3-compatible Artifact Driver | ⚠ DONE，审查要求 R1 | `wp-00c2-artifact-s3.md` | `be34bf58dd9778a5670561ece6990bc508edb0386b0265cadcb1cc487a801f06` | 2026-08-08 |
| WP-00 | 00c2-r1 S3 Driver 正确性整改 | ⚠ DONE，审查要求 R2 | `wp-00c2-r1-artifact-s3-correctness.md` | `10f4008387fac5696f0a1cce438189dae5392c4e94c5fff3d25a7732bca705c4` | 2026-08-08 |
| WP-00 | 00c2-r2 S3 stream/endpoint 最终整改 | ⚠ DONE，审查要求 R3 | `wp-00c2-r2-artifact-stream-endpoint.md` | `081126a9ddc31322c2d45bfd75bd1622461c436bce7eeb7ee5a58fb8b0774d7a` | 2026-08-08 |
| WP-00 | 00c2-r3 Provider ClientError traceback 脱敏 | ✅ DONE，审查通过 | `wp-00c2-r3-provider-error-redaction.md` | `acb0ed5796b9c1b76289c6dd984f5371dcdb70bb97ba8434e0d8b0990495679d` | 2026-08-08 |
| WP-00 | 00d1 Observability primitives | ⚠ DONE，审查要求 R1 | `wp-00d1-observability-foundation.md` | `cd10e0cf26b60077fc0d673a860b1576899684ac46ba5dadc0b44f559c53e427` | 2026-08-08 |
| WP-00 | 00d1-r1 Observability boundaries | ⚠ DONE，审查要求 R2 | `wp-00d1-r1-observability-boundaries.md` | `a8e6ce006f8859c3c1af820428ea2d174820a7b3387eaeb454b80453ff86cffd` | 2026-08-08 |
| WP-00 | 00d1-r2 Sensitive text coverage | ⚠ DONE，审查要求 R3 | `wp-00d1-r2-sensitive-text-coverage.md` | `e2d08336a9dff253e53782220c7e17b5a614b510eb934d4e7e8f4eaaff5edf8b` | 2026-08-08 |
| WP-00 | 00d1-r3 Redactor parser boundaries | ⚠ DONE，审查要求 R4 | `wp-00d1-r3-redactor-parser-boundaries.md` | `67b71a901da3942354509e8a7212d6607fe52bbe82c7574b4a68d2d466131015` | 2026-08-08 |
| WP-00 | 00d1-r4 Deterministic bounded redactor | ⚠ DONE，审查要求 R5 | `wp-00d1-r4-deterministic-redactor.md` | `4d8cf0200bcaaaa2d98a798adac4ab0a48c2e944505ed1f9932097a7155b0e2d` | 2026-08-08 |
| WP-00 | 00d1-r5 Redactor sensitive-key boundaries | ✅ DONE，审查通过 | `wp-00d1-r5-redactor-key-boundaries.md` | `4b1928363da625035fa714018e99ffec175c30c978fabb7d294e92f878753d3e` | 2026-08-08 |
| WP-00 | 00d2 Lifespan / health / metrics / Artifact factory | ⏳ pending | `wp-00d2-runtime-lifespan-health.md` | — | — |
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
4. `DONE` 是实现者交付状态，审查接受状态以 [`../tasks/README.md`](../tasks/README.md) 为准。

```bash
# 复验某个 manifest 的哈希
sed -e '/^[0-9a-f]\{64\}$/d' serve/docs/manifests/<manifest>.md | sha256sum
```

## 依赖链

`WP-00` → `WP-01A` → `WP-01B` → `WP-01C` → `WP-02` → `WP-03` → `WP-04` → `WP-05` → `WP-06`；`WP-07A/07B` 依赖各域对应 WP；`WP-08` 依赖全部。
