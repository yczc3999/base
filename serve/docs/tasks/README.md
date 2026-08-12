# V2 任务交接索引

> 本文件是当前任务的单一入口。实现结果索引见
> [`../manifests/README.md`](../manifests/README.md)。

## 当前任务

| 字段 | 当前值 |
|---|---|
| Task | `WP-07B` |
| 状态 | `BLOCKED_PRODUCT_VISUAL_DECISION` |
| 任务文档 | —（未创建；产品视觉确认前禁止创建） |
| 交付 manifest | — |
| 前置实现 | `WP-07A` 已 ACCEPTED；code commit=`280afcc`；head=`b1000070`；manifest SHA=`881ab05c448fc6b345d0df97738e756a50bd6af2064cefc6c3968b72fff9feb1` |
| 已接受范围 | Admin 全域只读 API、RBAC、HMAC keyset cursor、artifact Range、typed frontend API/query data layer、`0070` 权限与索引 |
| 接受证据 | 135 unit/config + 50 真 PG + 19 router；1794 trading、2005 full；frontend 20/lint/build；clean perf 20/20 PASS |
| 当前 blocker | 用户尚未确认产品专属 palette、语义颜色、字体、密度/spacing、圆角 token 与一张真实 Episode Detail 高保真预览 |
| 关键边界 | 严格平面、大块纯色；无阴影、渐变、glass/blur、highlight、浮卡或 lift/scale；确认前不创建 WP-07B task/页面 |
| 后续 | 展示视觉 token 与 Episode Detail 高保真预览并取得用户明确确认后，才创建 WP-07B READY 合同 |

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
| `WP-04` | DONE | ACCEPTED | 审查修复提交 `8ff2067`；63 unit + 57 真 PG/replay、1375 trading、1586 full，均 0 skip/fail；clean perf keyset 100,006 行 p99=3.254ms、scientific replay p99=21.218ms、rebuild hash一致且 lost/dup=0、498.493 q/s；生产 policy、CAS/full cashflow、midpoint/vector exclusion、exact cohort/五层/replay/cursor/DB guard 全关；manifest SHA `c22daa47…` |
| `WP-05` | DONE | ACCEPTED | 审查修复 `f53888f` + 性能证据 `5588576`；1566 trading、1777 full，均 0 skip/fail；clean perf 3024 intents/60.006s=50.395/s，WS p99=5.185ms、1k reconcile p99=91.043ms、pool peak exec2/recon1、fake/real=4706/0；Vault identity、fund consumption、全副作用 fencing、UNKNOWN 恢复、CONFIRMED trade、heartbeat hard-stop、NegRisk/clock/egress 全关；manifest SHA `04e365b4…c2fc9`；进入 WP-06 |
| `WP-06` | DONE | ACCEPTED | 初交 `de79edc`；审查修复 `53b4744`；108 unit/contract + 43 真 PG、1717 trading、1928 full，均 0 skip/fail；clean perf 660 ops/60.005s=10.999/s、1000 UNKNOWN 两轮全等且 blind resend=0、pool p95=.020ms、fake/real=215560/0；ABI/真实 registry、runtime/TX/finality/effect、Vault、wrong-chain、post-final audit 全关；manifest SHA `a2280e00…f868`；进入 WP-07A |
| `WP-07A` | DONE | ACCEPTED | 初交 `a1718c2`；审查修复 `280afcc`；135 unit/config + 50 真 PG + 19 router、1794 trading、2005 full，均 0 skip/fail；frontend 20 passed、lint 0 error、build pass；clean perf 20/20 PASS，100,008 行，深页 p95/p99=14.911/18.545ms，32 workers 60.177s、97.429 req/s、pool p95=.048ms，traversal lost/dup/out-of-snapshot=0；首屏 filter/as_of、3 endpoint、exact seed、read-only/RBAC/cache、AI artifact、timeline/zstd Range、DTO/frontend/shared 401 与真实 SLO harness 全关；manifest SHA `881ab05c…eb1`；WP-07B 保持 BLOCKED_PRODUCT_VISUAL_DECISION |
