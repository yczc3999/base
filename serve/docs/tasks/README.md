# V2 任务交接索引

> 本文件是当前任务的单一入口。实现结果索引见
> [`../manifests/README.md`](../manifests/README.md)。

## 当前任务

| 字段 | 当前值 |
|---|---|
| Task | `WP-04` |
| 状态 | `DONE（待审）` |
| 任务文档 | [`wp-04-learning-evaluation-read-projections.md`](wp-04-learning-evaluation-read-projections.md) |
| 交付 manifest | `serve/docs/manifests/wp-04-learning-evaluation-read-projections.md` |
| 前置实现 | `WP-03` 已接受；head=`b1000031` → 已到 `b1000041` |
| 本任务范围 | 0040/0041、label audit、canonical target、五层 metric、split/holdout、replay/G8、read projections |
| 内部 checkpoint | A evaluation spec、B 0040 learning facts、C evaluation/replay/G8、D 0041 projections/perf/full；一个 manifest |
| 关键边界 | 新闻类、shadow only、authorized capital=0、final-admissible-only proper loss、projection 非权威 |
| 交付证据 | 见任务 §7–§10；必须生成 P_EVALUATION_SPEC_MANIFEST + P3_COMPLETION_MANIFEST 两小节 |
| 后续 | WP-04 接受后进入 `WP-05` execution readiness；不得提前接私有 CLOB/账户/真实资金 |

## 固定交接协议

1. 实现者只读取并执行“当前任务”指向的文档，不从聊天记录猜范围。
2. 一个里程碑可含多个内部 checkpoint；实现者连续推进，不为 checkpoint 另建 task/manifest。
3. 全部 checkpoint 完成后生成任务文档指定名称的唯一 completion manifest，并更新索引为 `DONE`。
4. 用户只需回复 **“完成”**。审查者随后直接读取 Git、任务文档和 manifest，并复跑验收。
5. 范围内 P0/P1 由审查者直接修复并复验，仍归入原里程碑；不再反复创建 `-rN` 文档。
6. 只有产品决策、外部 blocker 或超出允许范围的架构重做，才创建整改任务并阻塞依赖链。
7. 审查通过：本表将当前任务标为 `ACCEPTED`，随后创建并指向下一里程碑文档。
8. 最终 manifest 与里程碑一一对应；接受后冻结，不再改写。

## 审查记录

| Task | 实现状态 | 审查结论 | 证据/后续 |
|---|---|---|---|
| `WP-00a` | DONE | ACCEPTED | 31 targeted tests；manifest SHA 一致 |
| `WP-00b` | DONE | REMEDIATION_REQUIRED | 见当前 `WP-00b-r1` 任务文档 |
| `WP-00b-r1` | DONE | REMEDIATION_REQUIRED | 功能修复有效；见当前 `WP-00b-r2` 任务文档 |
| `WP-00b-r2` | DONE | ACCEPTED | 49/80/291 tests；100x TTL；双前缀 0；SHA 一致；1 个 P2 转入 00c1 |
| `WP-00c1` | DONE | REMEDIATION_REQUIRED | 59 target、125 trading、336 full 均过；但 bounded decode/range/CAS head/fsync 发现 4 个 P1；见当前 R1 |
| `WP-00c1-r1` | DONE | REMEDIATION_REQUIRED | 83 target、149 trading、360 full 均过；stored 写前上限、unknown-frame EOF、durability retry 尚未关闭；见当前 R2 |
| `WP-00c1-r2` | DONE | ACCEPTED | 独立复验：35/24/18 targeted；159 trading；370 full；三 P1 全关；双前缀 0；SHA 一致 |
| `WP-00c2` | DONE | REMEDIATION_REQUIRED | 54 S3、242 trading、453 full 均过；但配置未控制 409 次数、未显式 SigV4、异常边界/created/Range 仍有 P1；见当前 R1 |
| `WP-00c2-r1` | DONE | REMEDIATION_REQUIRED | 91 S3、279 trading、490 full 均过；主体整改有效，但 body.read 原生异常泄出、endpoint 校验与 exists 完整性尚未关闭；见当前 R2 |
| `WP-00c2-r2` | DONE | REMEDIATION_REQUIRED | 114 S3、55 config、312 trading、523 full 与 SHA 均通过；PUT ClientError 三个分支未抑制 traceback context；见当前 R3 |
| `WP-00c2-r3` | DONE | ACCEPTED | 独立复验：117 S3、315 trading、526 full；三分支 traceback 脱敏全关；compileall/diff/SHA 通过 |
| `WP-00d1` | DONE | REMEDIATION_REQUIRED | 157 定向、417 trading、628 full 与 SHA 通过；span value 脱敏、坏 str 日志、provider 重配置生命周期三项 P1；见当前 R1 |
| `WP-00d1-r1` | DONE | REMEDIATION_REQUIRED | 实际 20 log + 38 trace、444 trading、655 full 与 SHA 通过；private-key/body/tool/raw/token/Set-Cookie 仍泄露；见当前 R2 |
| `WP-00d1-r2` | DONE | REMEDIATION_REQUIRED | 61 log、80 trace、527 trading、738 full 与 SHA 通过；长 PEM/quoted value/Cookie 解析边界仍泄露；见当前 R3 |
| `WP-00d1-r3` | DONE | REMEDIATION_REQUIRED | 91 log、80 trace、557 trading、768 full 与 SHA 通过；未闭合 quote 相反/转义引号仍泄露；见当前 R4 |
| `WP-00d1-r4` | DONE | REMEDIATION_REQUIRED | 101 log + 80 trace、567 trading、778 full 与 SHA 通过；`prefix token=` / `INFO Cookie:` 等前导文本使敏感 key 被贪婪吞并，见 R5 |
| `WP-00d1-r5` | DONE | ACCEPTED | 独立复验 134 log、88 trace、93 config、33 metric、608 trading、819 full；prefix/multi-key/negative/fuzz 与 SHA/diff 全过，无 P0/P1 |
| `WP-00d2` | DONE | REMEDIATION_REQUIRED | 正常路径与 852 full 通过；但 lifespan body 异常时所有 cleanup=0，basicConfig/raw exception 绕过 redactor，首次/关闭状态与固定 schema 不正确；见 R1 |
| `WP-00d2-r1` | DONE | REMEDIATION_REQUIRED | 主体整改有效；tracing 初始/关闭、health-close 竞态、malformed probe 仍有 P1；见 R2 |
| `WP-00d2-r2` | DONE | ACCEPTED | 61 定向、669 trading、880 full；tracing/race/malformed 全关；无新 RuntimeWarning；SHA 一致 |
| `WP-01A-00` | DONE | REMEDIATION_REQUIRED | 原交付 14 单测 + 3 真 PG + 897 full 均过，但 allowlist 未接入、外部事务所有权及并发/整 run rollback 证明存在 5 个 P1；见 R1 |
| `WP-01A-00-r1` | DONE | ACCEPTED | 15 unit + 3 真 PG + 687 trading + 898 full；allowlist/default schema、clean connection、URL origin、真并发锁、整 run rollback 全关；manifest SHA 一致 |
| `WP-01A-01` | DONE | REMEDIATION_REQUIRED | 40 unit + 6 真 PG + 718 trading + 929 full 均过；但 offline PK 校验丢失 composite ordinal，反序主键仍错误升级；见 R1 |
| `WP-01A-01-r1` | DONE | REMEDIATION_REQUIRED | ordinal 生产修复正确；但 offline 反例从无 version 表开始，未证明失败后保持 baseline；见 R2 |
| `WP-01A-01-r2` | DONE | ACCEPTED | 三组反序 PK 从真实 baseline 应用 `cdabba1e3903:head` 均拒绝并保持 baseline；27 unit、13 真 PG、727 trading、938 full |
| `WP-01A-02a` | SUPERSEDED | — | 未实施；已合并进 `WP-01A-02`，避免微任务交接 |
| `WP-01A-02` | DONE | ACCEPTED | 审查者直接关闭 10 类 P1；35/7/48/29 定向，833 trading，1044 full；固定 migration DDL、DB immutability、UoW/Outbox crash/idempotency/transport 边界全过；manifest SHA 一致 |
| `WP-01B` | DONE | ACCEPTED | 70 wire + 30 真 PG/replay；paced 1k/s×60s、5k/s×10s 均过；933 trading、1144 full；四链 frame、lease/fence resume、epoch barrier、raw evidence/replay 全关；manifest SHA `06b01cdd…` |
| `WP-01C` | DONE | ACCEPTED | 98 定向、1242 full；50k enrollment+R0=8.714s，完整 pipeline=144.99/s 持续 67.046s；Gate policy exact binding、contract/component/cohort/episode DB 不变量与 replay 全过；manifest SHA `4a17a08a…` |
| `WP-02` | DONE | ACCEPTED | 审查修复提交 `e9f4c20`；218 定向、1390 full，均 0 skip/fail；AI 197.782/s、blind commit 59.711/s 持续 60s；完整 Artifact/lineage、global attempt identity、exact cache 与 runtime 边界全关；manifest SHA `5bc49cf3…` |
| `WP-03` | DONE | ACCEPTED | 审查修复提交 `2a22ff9`；69 unit + 17 真 PG/replay、1255 trading、1466 full，均 0 skip/fail；VAL 206.796/s、TERM 12.0/s 持续 60s；DB-authoritative Q/quote/cost/cap、intent、BUY/SELL ledger、reversal、V1 Gold 边界全关；manifest SHA `996869e2…` |
| `WP-04` | DONE | 待审 | 57 unit + 49 真 PG/replay、1361 trading、1572 full，均 0 skip/fail；keyset 100k 行 p99=2.93ms、replay p99=57.9ms、rebuild hash 一致、470 q/s 持续 10s；label 五态 fail-closed、canonical target、五层 metric、split/holdout、G8 future-effective、只读投影全关；manifest SHA `1d8a9083…` |
