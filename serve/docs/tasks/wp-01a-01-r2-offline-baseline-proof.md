# WP-01A-01-r2 — Offline baseline 失败证明修正

> 状态：**ACCEPTED**（审查者直接整改）。完成 manifest：
> `serve/docs/manifests/wp-01a-01-r2-offline-baseline-proof.md`。
> 最后更新：2026-08-10 EDT。

## 1. 目标与结论

R1 的 ordinal 生产修复正确，但其 offline 反例从“无 version 表”开始，未证明任务要求的
“失败后 version 仍为 `cdabba1e3903`”。本次只修正验收路径：先建立真实 baseline，再应用
`cdabba1e3903:head --sql` range；三组反序复合主键均失败并保持 baseline。

## 2. 精确变更与证据

修改以下证明/fixture 文件，不改生产代码：

- `_generate_offline_sql()` 支持显式 revision/range；
- offline swapped-PK 与 canonical 路径都先 `_prepare_baseline()`；
- 应用 `cdabba1e3903:head`，失败断言 version 仍为 baseline、public 表数 19；
- `base_legacy_schema.sql` 删除 EOF 多余空行并保留单个 LF；同步更新 fixture manifest SHA，
  使 staged `git diff --check` 真正为零；canonical schema 签名不变；
- 不修改 R1 生产代码、revision、env 或原 completion manifest。

复验：27 unit、13 真 PostgreSQL integration、727 trading、938 full，9 个既有弃用告警；
`compileall`、`git diff --check`、manifest SHA 均通过。无 P0/P1。

## 3. 非目标与回滚

不扩展 schema 合同，不创建 `trading/0002`。回滚仅撤销上述 integration test 的 range/baseline
差异；无数据库或业务数据副作用。
