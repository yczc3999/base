# V2 任务交接索引

> 本文件是当前任务的单一入口。实现结果索引见
> [`../manifests/README.md`](../manifests/README.md)。

## 当前任务

| 字段 | 当前值 |
|---|---|
| Task | `WP-01A-02` |
| 状态 | `READY` |
| 任务文档 | [`wp-01a-02-trading-foundation.md`](wp-01a-02-trading-foundation.md) |
| 交付 manifest | [`wp-01a-02-trading-foundation.md`](../manifests/wp-01a-02-trading-foundation.md) |
| 前置实现 | `WP-01A-01-r2` 已接受；`v2_0001` 完成 |
| 前置审查 | 27 unit + 13 真 PG + 727 trading + 938 full；offline range 保持 baseline；无 P0/P1 |
| 本任务范围 | 一次完成 Trading ORM kernel、foundation models、`v2_0002`、UoW 与可靠 Outbox |
| 内部 checkpoint | A ORM kernel → B 20 表 Models → C Migration → D UoW/Outbox + 集成验收；中间不等待用户、不拆 manifest |
| 后续 | 验收后直接进入 `WP-01B` Polymarket public market data 里程碑 |

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
| `WP-01A-02` | READY | PENDING | 当前加速里程碑；Trading foundation 全闭环 |
