# V2 任务交接索引

> 本文件是当前任务的单一入口。实现结果索引见
> [`../manifests/README.md`](../manifests/README.md)。

## 当前任务

| 字段 | 当前值 |
|---|---|
| Task | `WP-00d1` |
| 状态 | `READY` |
| 任务文档 | [`wp-00d1-observability-foundation.md`](wp-00d1-observability-foundation.md) |
| 前置实现 | `WP-00c2-r3`（ACCEPTED） |
| 前置审查 | 独立复验 117 S3、315 trading、526 full；三条 ClientError traceback 脱敏全关；manifest SHA 一致 |
| 实现范围 | typed observability config + JSON/redaction logging + low-cardinality Prometheus + W3C/OTel tracing primitives |
| 后续候选 | `WP-00d2 Lifespan / health / metrics endpoint / Artifact factory`，00d1 接受前不得创建 |

## 固定交接协议

1. 实现者只读取并执行“当前任务”指向的文档，不从聊天记录猜范围。
2. 完成时必须生成任务文档指定名称的 completion manifest，并更新 manifests 索引为 `DONE`。
3. 用户只需回复 **“完成”**。审查者随后直接读取 Git、任务文档和 manifest，并复跑验收。
4. 审查通过：本表将当前任务标为 `ACCEPTED`，随后创建并指向下一份任务文档。
5. 审查失败：本表标为 `REMEDIATION_REQUIRED`，创建整改任务；不得推进业务依赖链。
6. P0/P1 问题单独整改；不与新功能混做。仅不影响正确性的 P2 可写入下一任务的强制前置项。
7. 任务文档和 manifest 一一对应；manifest 一旦哈希冻结不再改写，纠错创建 `-rN` 新文档。

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
| `WP-00d1` | — | READY | 技术可观测性 primitives；接受前不得推进 lifespan 集成 |
