# AI 调用可观察与回放设计

> 目标：任何一次 AI 调用都能回答“为什么调用、给了什么、做了什么、返回什么、
> 是否可信、影响了什么、花了多少”，并能在未来使用冻结输入做模型、Prompt 和策略对比。

## 1. 硬规则

1. 每次调用先创建 `invocation_id`，再访问 Provider；没有调用记录就没有业务结果。
2. 一次调用尝试对应一条独立记录；重试、fallback、人工重跑都必须创建新 attempt。
3. 保存完整输入 manifest、原始请求、工具过程、原始响应、解析结果、校验结果和下游绑定。
4. 已完成调用不可覆盖或删除；纠正使用新 revision/event。
5. 普通日志不是事实源；PostgreSQL 元数据 + content-addressed Artifact Store 才是事实源。
6. API key、Authorization、Cookie 等秘密在持久化前移除；不能依靠查看页面时临时遮挡。
7. Blind 调用必须保存输入分类和污染检查，证明上下文没有 quote/odds/crowd。
8. 历史重跑永远创建 challenger 结果，不回写当时的生产判断。

## 2. 一次调用的完整生命周期

```text
PLANNED
→ STARTED
→ TOOL_RUNNING（可重复）
→ RESPONSE_RECEIVED
→ PARSED
→ VALIDATED
→ ACCEPTED | REJECTED

异常终态：FAILED | TIMEOUT | CANCELLED | UNKNOWN
```

- `RESPONSE_RECEIVED` 只表示 Provider 有返回，不表示结果可用；
- schema、时态、污染、概率和业务 Gate 全过后才能 `ACCEPTED`；
- Worker 崩溃后仍为 `STARTED/UNKNOWN` 的 attempt 不得猜测结果，重试创建新 attempt；
- fallback 记录 `fallback_of_invocation_id + reason_code`，不得拼接两个模型的答案。

## 3. 数据对象

### 3.1 `ai_invocations`：一次模型 attempt

| 字段组 | 必存内容 |
|---|---|
| 身份 | `id、chain_id、episode_id、stage、role、attempt_no、experiment_variant_id` |
| 因果 | `parent_invocation_id、retry_of、fallback_of、causation_event_id` |
| 版本 | `release_manifest_id、strategy_version_id、config_version_id、git_sha、db_revision` |
| 模型 | requested/returned provider、route、model、effort、sampling、seed |
| 权限 | network policy、允许的 tools/domains、blind/revealed context class |
| Prompt | system/developer/user/tool schema 的 version、artifact ID 和 hash |
| 输入 | information snapshot、contract/component/prior/evidence IDs、canonical manifest hash |
| 输出 | raw response、parsed output、normalized output artifact ID/hash |
| 状态 | lifecycle state、result、reason/error code、是否 retriable |
| 时间 | queued/start/first-token/response/parsed/validated/completed timestamps |
| 用量 | input/cache/output/reasoning tokens、tool/search calls、provider request ID |
| 成本 | estimated/billed cost、currency、pricing snapshot、reconciliation status |
| 结果 | schema/taint/business validation summary、accepted output binding |

`requested_model` 与 `returned_model` 分开保存，用于识别 relay alias 漂移，例如请求
`grok-4.5` 却返回 `grok-4.5-build`。

### 3.2 `ai_tool_calls`：每次工具调用

每条保存：

```text
invocation_id, ordinal, tool_type, tool_version
arguments_artifact_id/hash
started_at, completed_at, status, error_code
result_artifact_id/hash
source_urls, published_at/observed_at
usage, cost, provider_tool_call_id
```

Web/X 搜索必须保存查询、过滤条件、实际打开的来源和最终引用关系；“模型声称搜索过”
但没有 tool receipt/source artifact，不算可验证研究。

### 3.3 `ai_validation_results`：结果为什么可用或不可用

每个 Validator 单独一条：

- JSON/schema parser；
- secret/PII redactor；
- information-cutoff 与三时态；
- blind taint；
- contract/schema/probability coherence；
- evidence coverage；
- role-specific business Gate。

字段包括 `validator_name/version、passed、severity、reason_code、details_artifact_hash`。
任一 hard validator 失败，调用不能形成可供下游使用的 artifact。

### 3.4 `artifact_lineage_edges`：结果影响了什么

```text
from_artifact_id → to_artifact_id
relation = READS | PRODUCES | VALIDATES | SUPERSEDES | PROJECTS_TO | USED_BY
invocation_id, event_id, created_at
```

借此可以从一笔交易反查到 forecast、evidence、工具来源和模型调用，也可以从某条错误
事实正向找出受影响的所有 prediction/decision。

### 3.5 `artifact_objects`

大对象统一保存：canonical request、raw response、tool result、网页/PDF、prompt、结构化输出、
validation report。元数据至少包含：

```text
artifact_id, kind, schema_version, sha256, size, mime
storage_uri, compression, encryption_key_version
visibility_class, information_time, created_by_attempt
created_at, retention_class, quarantine_state
```

相同内容按 SHA-256 去重；业务表只引用 artifact ID，不复制大段 JSON。

## 4. 输入和输出到底保存什么

### 4.1 输入

- 发送给模型的完整消息序列，包含 system/developer/user/tool schema；
- 输入中引用的 contract、prior、facts、sources 和 snapshot 的精确版本；
- 工具/联网权限和域名 allowlist；
- token/output/search 预算、timeout、sampling 参数；
- canonical serialization 和 hash；
- 所有字段的 context class：`CONTRACT / PRIOR / EVIDENCE / QUOTE / ODDS / CROWD /
  LABEL / FUTURE_FACT`。

Blind role 的 allowlist 只接受前三类合格对象；污染检查报告随调用永久保存。

### 4.2 输出

- Provider 原始 envelope 和 response body；
- reasoning token 数等 Provider 元数据，但不假设 Provider 会返回隐藏思维链；
- 模型显式提供的 reasoning/claims/citations/概率；
- parser 生成的结构化对象；
- normalization 前后差异；
- Validator 结果；
- 最终是 `accepted、rejected、deferred、abstain`，以及生成了哪个业务 artifact。

不得只保存最后的概率数字，否则未来无法判断错误来自证据、Prompt、模型、解析还是归一化。

## 5. 成本与性能

每次调用同时记录三套费用：

1. **Provider reported usage**；
2. 按冻结 `pricing_snapshot` 计算的 estimated cost；
3. 从账单/余额变化得到的 billed cost。

人民币中转站使用实际充值口径，不能把页面美元符号重新换算。缓存、search/tool、失败调用和
重试成本单列。聚合指标按 role/provider/model/route/prompt/cohort/category 输出：调用数、
成功率、schema 首次通过率、p50/p95/p99、tokens、工具次数、成本和单位 accepted artifact 成本。

## 6. Admin 页面

新增独立菜单 **AI 调用记录**，不能只藏在普通日志里。

### 调用列表

筛选：时间、role、stage、provider/route/model、Episode、状态、validator、是否 fallback、
blind/revealed、成本和延迟。

列：调用原因、模型、状态、开始时间、耗时、token/tool、成本、输出类型和下游影响。

### 调用详情（隐藏路由）

```text
调用摘要
→ 配置/代码/Prompt 版本
→ 输入 manifest 与污染分类
→ 工具时间线及来源
→ 原始响应（脱敏、按权限查看）
→ 解析/标准化结果
→ Validator 与 Gate
→ 下游 artifact/decision
→ token、成本、延迟
→ retry/fallback/experiment 关系
```

从 Episode、Decision、Cost 和 Audit 页面都能一跳进入该详情；详情页面不提供编辑或删除。

## 7. 三种回测/回放

### A. 原样回放

不调用模型，读取原始 response，使用新 parser/validator 或原版本重新处理。用于定位解析、
归一化和下游代码变化；确定性结果必须可比较 hash。

### B. 模型/Prompt 对比

将当时冻结的同一 input manifest 交给新模型或新 Prompt，生成独立 challenger invocation。
禁止联网补充未来资料，禁止读取当时尚未公开的 label/quote。比较：

- schema/事实错误率；
- prediction proper loss 与 tail loss；
- 对最终 decision 的增量影响；
- 延迟、成本和 System Net Profit。

### C. 下游策略对比

保持当时 AI 输出不变，只替换 shrinkage、edge、成本、仓位或 Gate 配置，重算 decision 和
shadow PnL。这样才能区分“AI 判断错”和“下注逻辑错”。

所有回测写入 `replay_runs/experiment_variants`，绝不修改原 Episode。

## 8. 保留、脱敏与权限

- invocation metadata、committed forecast/decision 相关 artifact 默认永久保留；
- 其他成功、失败和 rejected attempt 也默认保留，90 天后可转冷存储，不删除 lineage/hash；
- 安全或法律清除必须留下 tombstone event、原因、操作者和受影响 artifact 清单；
- 保存前递归移除 authorization、api_key、token、cookie、password、secret；
- 检出疑似秘密的 artifact 进入 quarantine，不进入普通查询和回放；
- 原始请求/响应查看、导出和 replay 分别设置 RBAC 权限并记录 admin audit event。

建议权限：

```text
admin:ai_invocation:list/detail/export_raw/replay
admin:ai_artifact:view_raw
admin:model_route:test
```

## 9. 原子性与失败恢复

调用开始前事务写入 invocation + `STARTED` event。收到响应后，单一事务写入 response
artifact reference、parsed result、validators、lineage、final event 和 outbox。

- 同一 `invocation_id` 不重复调用 Provider；
- `idempotency_key` 防止同一 stage 意外创建两个有效 attempt；
- Provider 已返回但 Worker 未持久化时，该 attempt 标记 `UNKNOWN`，只能新建 retry；
- 下游只消费 `ACCEPTED` 且 artifact hash 校验通过的输出；
- Audit writer 失败必须使业务阶段失败，不能静默丢审计记录后继续交易。

## 10. 验收标准

1. 任取一条 forecast 或 decision，可在一次查询链中找到所有 AI attempts 和精确输入输出。
2. 任取一次 Web/X 研究，可看到查询、工具 receipt、来源、时间和被引用的 claim。
3. requested/returned model、Prompt、配置、代码、usage、成本和延迟完整率为 100%。
4. Blind 调用的 quote/odds/crowd/future-label 污染数为 0。
5. timeout、retry、fallback 均是独立 attempt，关系完整，无静默替换。
6. AI 输出被采用或拒绝都有 Validator/Gate reason；不存在“结果存了但不知道是否使用”。
7. 用冻结 input manifest 运行 challenger，不会读取当时之后的事实或标签。
8. 原样回放可重现 parser、normalization、projection 和 decision hash。
9. 成本可从系统总额下钻到单次 invocation，并与余额/账单对账。
10. 删除、编辑 committed invocation/artifact 的 API 和数据库路径均不存在。
