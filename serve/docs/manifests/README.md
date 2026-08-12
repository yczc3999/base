# Polymarket V2 — 工作包完成索引（审查入口）

> 每个 `WP-*` 里程碑完成后必须在此登记，并附一份最终 completion manifest；里程碑内部
> checkpoint 不另建 manifest。模板见 `serve/docs/v2-implementation-contract.md` §13。

## 当前状态

> **WP-00 / WP-01A / WP-01B / WP-01C / WP-02 / WP-03 / WP-04 / WP-05 / WP-06：✅ COMPLETE**；`WP-07A` ⏳ pending。

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
| WP-00 | 00d2 Lifespan / health / metrics / Artifact factory | ⚠ DONE，审查要求 R1 | `wp-00d2-runtime-lifespan-health.md` | `5b9d88e4414ab49f7df9e68999b082b80a143c07faa26d0e44595cd949466135` | 2026-08-09 |
| WP-00 | 00d2-r1 Runtime failure boundaries | ⚠ DONE，审查要求 R2 | `wp-00d2-r1-runtime-failure-boundaries.md` | `7a18da989ea994a774a494319fd95e0e3c37ccb496fc16226db612ec67acbc3d` | 2026-08-09 |
| WP-00 | 00d2-r2 Runtime final boundaries | ✅ DONE，审查通过 | `wp-00d2-r2-runtime-final-boundaries.md` | `79998a9cc56cca8434808405d1cc1b9caa04aa4bb75bd50eb1b0df8b0dbdd05e` | 2026-08-09 |
| WP-01A | 01A-00 Alembic 执行基础 | ⚠ DONE，审查要求 R1 | `wp-01a-00-alembic-execution-foundation.md` | `48b32065940cdc9bba0a2ac3ebc4e520e9fbae2674e7ce2537ce7843eb374af5` | 2026-08-09 |
| WP-01A | 01A-00-r1 Alembic 证明边界整改 | ✅ DONE，审查通过 | `wp-01a-00-r1-alembic-proof-boundaries.md` | `88091118c83be3b3305be84cc26d771e25f07067f80d17592683ac436c01a3bb` | 2026-08-09 |
| WP-01A | 01A-01 Base schema 兼容合同与 `v2_0001` | ⚠ DONE，审查要求 R1 | `wp-01a-01-base-schema-contract.md` | `82c14364a9ae9885e015668b108a9313c923b97b655deea836c26fce44076001` | 2026-08-09 |
| WP-01A | 01A-01-r1 Offline composite-PK 顺序整改 | ⚠ DONE，审查要求 R2 | `wp-01a-01-r1-offline-pk-order.md` | `a077e3336eb4c5955c3f1ce23fbbfe76a15e972e6eb76c6bf43e46d296ed332a` | 2026-08-10 |
| WP-01A | 01A-01-r2 Offline baseline 失败证明 | ✅ DONE，审查通过 | `wp-01a-01-r2-offline-baseline-proof.md` | `57991d05853f5c1f752eeabc68aaf0854869e54d0f015b0c3272f781f49fb8bd` | 2026-08-10 |
| WP-01A | 01A-02a Trading ORM kernel | ↪ SUPERSEDED（未实施，合并） | — | — | — |
| WP-01A | 01A-02 Trading foundation：ORM/0002/UoW/Outbox | ✅ DONE，审查通过 | `wp-01a-02-trading-foundation.md` | `b5aeeffbd87a16373d91557a24d4501d91453e79572a372578123c3921305aa8` | 2026-08-10 |
| WP-01B | 0010/0011 Gamma/CLOB schema/Driver、universe/book ingest | ✅ DONE，审查通过 | `wp-01b-public-market-data.md` | `06b01cdd60ead9657756f01c2b890064211c8e98bfaaae992543792bf9b8c4a2` | 2026-08-10 |
| WP-01C | 0012/0013 contract/component/cohort/screening | ✅ DONE，审查通过 | `wp-01c-contract-component-cohort-screening.md` | `4a17a08acffa3380f0fa37ac6b7ba592c48dbe80df91e07e30c24ef0c5e7c9c4` | 2026-08-11 |
| WP-02 | 0020/0021 AI invocation/model gateway/evidence/forecast | ✅ DONE，审查通过 | `wp-02-minimal-cognition-ai-observability.md` | `5bc49cf3db17b3e42b46f136b1a5bc3569694cb89ee5cee40cfc96707f29d316` | 2026-08-11 |
| WP-03 | 0030/0031 decision/portfolio/shadow execution/ledger | ✅ DONE，审查通过 | `wp-03-market-relative-decision-shadow-ledger.md` | `996869e25bf818d0fe58b2463a6784a477f43c15b508fa1ec78d0e28621822b5` | 2026-08-11 |
| WP-04 | 0040/0041 label/evaluation/replay/promotion/read projections | ✅ DONE，审查通过 | `wp-04-learning-evaluation-read-projections.md` | `c22daa477f748354538c484fff5957e237a0f03db39907c2767580e957bf638a` | 2026-08-11 |
| WP-05 | 0050/0051 vault/account/private CLOB/User WS/reconcile | ✅ DONE，审查通过 | `wp-05-execution-readiness-private-clob.md` | `04e365b4b1c18dc529dd2f6aa73c0cccf29c6a6cab5487787776f74a9bdc2fc9` | 2026-08-11 |
| WP-06 | 0052 Polygon/relayer/settlement | ✅ DONE | `wp-06-polygon-relayer-settlement.md` | `18c0a510c15fdb23e134232443b293b309d9896ad2ca8b23731ae2199a5841d8` | 2026-08-11 |
| WP-07A | Admin API + frontend types/query scaffolding | ⏳ pending | — | — | — |
| WP-07B | 14 菜单页、5 详情页与交互 | ⏳ pending | — | — | — |
| WP-08 | 0090 分区/归档/perf harness/alerts/soak | ⏳ pending | — | — | — |

## 审查方式

1. 打开目标 WP 的 manifest，核对：修改文件（§1）、实现内容（§2）、命令与真实结果（§3）、配置/预算证据（§4）、未解决 blocker（§5）、回滚方式（§6）、manifest 路径 + SHA（§7）。
2. 先复跑最能证明核心不变量的定向测试，再跑里程碑集成/全量回归；范围内问题由审查者直接修复并计入同一 manifest，不生成 R 链。
3. SHA-256 口径以目标 task 的 manifest 合同为准。WP-05 必须按 task §9：只删除且只删除最后一行
   `COMPLETION_MANIFEST_SHA256: <64 lowercase hex>`（连同行尾 LF）后计算；不得使用会误删 artifact hash 的
   “删除所有 64 位 hex 行”规则。WP-00～04 的 legacy bare-hash manifest 继续使用各自已冻结口径。
4. `DONE` 是实现者交付状态，审查接受状态以 [`../tasks/README.md`](../tasks/README.md) 为准。

```bash
# 精确复验 WP-05 manifest
python3 - <<'PY'
from hashlib import sha256
from pathlib import Path
import re

p = Path("serve/docs/manifests/wp-05-execution-readiness-private-clob.md")
raw = p.read_bytes()
payload, count = re.subn(
    rb"(?m)^COMPLETION_MANIFEST_SHA256: [0-9a-f]{64}\n\Z",
    b"",
    raw,
)
assert count == 1
print(sha256(payload).hexdigest())
PY
```

## 依赖链

`WP-00` → `WP-01A` → `WP-01B` → `WP-01C` → `WP-02` → `WP-03` → `WP-04` → `WP-05` → `WP-06`；`WP-07A/07B` 依赖各域对应 WP；`WP-08` 依赖全部。
