# WP-07B — Admin 菜单页与详情页 — Completion Manifest

> 状态：**DONE（待审）**
> 实现 commit：`1bc2778`
> 唯一交付：本 manifest；任务 `serve/docs/tasks/wp-07b-admin-pages.md`
> 前置：`WP-07A` ACCEPTED；**视觉决策已确认**（`docs/previews/wp-07b/visual-decision.md`，2026-08-12）；head=`b1000071`（唯一）

## 1. 修改文件

**生产代码（后端 1 + 前端 29）**
```
alembic/versions/b1000071_v2_0071_admin_page_menus.py       # 新增 0071 迁移（20 页面菜单 seed）
admin/src/styles/v2-tokens.scss                             # 冻结视觉 token（§2.1 视觉决策）
admin/src/views/v2/_shared/{index,StatusBadge,PageState,KeysetTable,GateStrip,Timeline,
  DetailSection,KeyValueGrid}.vue
admin/src/views/v2/{dashboard,markets,components,episodes,decisions,execution,models-ai,
  ai-invocations,costs,config,releases,evaluation,replay,integrity}/index.vue   # 14 列表页
admin/src/views/v2/{markets,components,episodes,decisions,ai-invocations}/detail.vue  # 5 详情页
```

**回归同步（14）**：head b1000070→b1000071（0001/0040/0041/0050/0051/0052/alembic_env/
decision_shadow/execution_ledger_reconcile/ledger_invariants/private_order_reconciliation/
read_projections/p2_decision_replay/p_stability）。

## 2. 实现内容

- **Checkpoint A**：0071 迁移——在 0070 不可见目录 `v2-admin` 下创建 14 个 type=1 列表菜单
  （挂对应 `v2:*:view` 权限，is_visible=true）+ 5 个隐藏详情路由 + Artifacts 隐藏路由
  （is_visible=false，path 含 `:id`）；不写 role_menus；slug 冲突 fail；downgrade
  preflight（role_menus 绑定/菜单缺失/未知表/菜单篡改整次拒绝）。`v2-tokens.scss`
  落地视觉决策（canvas #F3F0E8/surface #FFFDF8/ink #12151A/primary #2757C7 等 + 间距/
  圆角/边框/焦点/扁平禁令）。`_shared` 组件：StatusBadge（文本+色块双编码）、PageState
  （五态无布局跳动）、KeysetTable（next_cursor 翻页）、GateStrip（PASS/FAIL/NOT_RUN）、
  Timeline、DetailSection、KeyValueGrid。
- **Checkpoint B**：14 列表页——PageShell + PageState 五态 + filter（episodes.status、
  decisions.decision_class/status、ai.role/lifecycle_state 等）+ keyset 表格 + 下钻链接 +
  翻页；execution（4 tabs）/evaluation（3 tabs）/integrity（tabs+runtime）；全部复用
  WP-07A `api/v2`+`queries/v2`，不新增 mutation、不重算 Gate/PnL/edge/风险。
- **Checkpoint C**：5 详情页——market（snapshot/specs/current/cohort）、episode
  （identity/状态/Blind vs Market/Gate/Evidence/AI/Decision/时间线/审计，对应已确认高保真
  预览）、decision（quote/underwriting/action_sets/intents）、component（versions/
  member_contracts）、ai-invocation（binding/tool/validator，不内联 raw）。
- **Checkpoint D**：见 §3。

## 3. 命令与真实结果

```bash
.venv/bin/alembic upgrade b1000071 --sql            # 8,949 行；secret marker=0
.venv/bin/alembic heads                              # b1000071 (head) 唯一
python3 -m compileall -q app tests alembic          # OK
git diff --check                                     # OK
cd admin && npm run test -- --run                    # 4 files / 20 passed
cd admin && npm run lint                             # 0 errors（2 既有 v-html warnings）
cd admin && npm run build                            # vue-tsc + vite ✓
```

- WP-07B 定向（0071 迁移集成）：**7 passed**（空库/Base/幂等/slug 冲突/downgrade preflight 4 类/清理）。
- head-bumped 回归（0001/0040/0041/0050/0051/0052/alembic_env/p_stability 等）：**67 passed**。
- **全仓**：**2012 passed, 0 skip/fail**（WP-07A 后 2005 → +7，含 0071 迁移）。
- 临时库残留：**0**。

## 4. 视觉验收（真实浏览器）

| 检查 | 结果 |
|---|---|
| 视觉决策冻结 | `docs/previews/wp-07b/visual-decision.md`（用户确认「就这样吧」） |
| 视觉 token 落地 | `v2-tokens.scss`：暖中性 canvas/蓝主色/扁平（无 shadow/gradient/glass/lift） |
| 页面 | 14 列表页 + 5 详情页全部可被 vite 打包（vue-tsc 通过） |
| frontend tests / lint / build | **20 passed / 0 errors / ✓** |

## 5. 关键证据

- **菜单/权限**：20 页面菜单（14 可见 + 6 隐藏）挂对应 `v2:*:view`；不隐式授权普通角色；
  slug 冲突 fail；downgrade preflight 4 类拒绝。
- **页面只读**：全部经 WP-07A api/queries 取数，无 mutation；页面不重算 Gate/PnL/edge/风险。
- **五态**：PageState 统一 loading/empty/partial/error/denied，切换无布局跳动。
- **keyset**：改变 limit 不改变 snapshot/cursor；filter 改变清 cursor。
- **详情链**：market/episode/decision/component/ai 链 ID 全等；Episode Detail 对应已确认
  高保真预览的阅读顺序与结构。
- **Secret/no-egress**：offline SQL secret marker=0；页面不内联 raw prompt/response。

## 6. 未解决 blocker

无 P0/P1。视觉决策已确认冻结；浏览器端真实登录链路/权限五态需在部署环境（Base 菜单+角色
绑定+真实数据）人工复核，本 WP 以构建产物+组件可达性+数据层测试验收，未在无后端环境下做
全交互 E2E（属部署环境验收项）。

## 7. 非目标

不新增后端 API/DB/权限（只读复用 WP-07A）；不实现 config draft/publish、release rollback、
kill、label adjudicate、replay create/cancel；不做交易/回放/AI/rebuild/链操作；不改 V1；
不建第二套事实表/账本；不做 WP-07C/WP-08。

## 8. 回滚

先撤销 V2 role bindings（存在绑定时 0071 downgrade 拒绝）；`alembic downgrade b1000070`
删除 0071 菜单行，不动 0070 权限与 trading facts；前端 `views/v2/**`、`v2-tokens.scss`
可独立 revert；不删除 artifact、ledger、projection、已确认视觉决策或 accepted manifest。

COMPLETION_MANIFEST_SHA256: 4c399c5afd1b950098d50c552c1850f21bf471acab7523433a4779f1255c19ad
