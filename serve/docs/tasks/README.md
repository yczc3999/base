# V2 任务交接索引

> 本文件是当前任务的单一入口。实现结果索引见
> [`../manifests/README.md`](../manifests/README.md)。

## 当前任务

| 字段 | 当前值 |
|---|---|
| Task | `WP-00b-r1` |
| 状态 | `READY — REMEDIATION_REQUIRED` |
| 任务文档 | [`wp-00b-r1-redis-foundation-remediation.md`](wp-00b-r1-redis-foundation-remediation.md) |
| 前置实现 | `WP-00b` |
| 前置审查 | 26 tests passed；manifest hash 一致；发现 3 项基础不变量缺陷和 1 项边界偏差 |
| 后续候选 | `WP-00c1 Artifact Store contracts/service/local`，本任务接受前不得创建 |

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
