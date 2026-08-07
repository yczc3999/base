# Design-Is Audit Scope — 2026-08-06

## Target
- **What**: Base Platform 后台管理系统（admin SPA）
- **Repo**: `/code/base/admin`（Vue 3 + TS + Element Plus + Vite）
- **Live URL**: `http://localhost:9200`（dev server confirmed running）
- **Audited surfaces**（登录后可访问的真实业务页面）:
  1. Login page — `src/views/login/index.vue`
  2. Dashboard — `src/views/dashboard/`
  3. 一个典型 CRUD 页（content/article 或 system/user）— 走 CrudTable 声明式
  4. 一个 SettingForm 页（settings/site 或 settings/ai）
  5. 全局 layout（sidebar + header + breadcrumb）

## Primary user
Base Platform 的下游业务系统管理员 / 运营人员。

## Primary task
高频内容管理 + 系统配置 + 用户/权限管理。关键交互链路 = CRUD（列表→筛选→编辑→保存）。

## Constraints
- 设计规范：圆角 ≤4px、扁平无阴影、边框区分层级、Primary #2563EB、字体 Geist、所有按钮带 Lucide 图标
- Element Plus 全覆写：`src/styles/element-override.scss`
- 用户的视觉偏好（project-level CLAUDE.md）：**大色块、纯平面，禁止阴影/渐变/玻璃模糊/高光/浮起/内凹**
- 栈约束：Element Plus + Vue 3 + TS

## User's question
"视觉上、优雅上、交互上是否已经达到「精致、遥遥领先」，能通过乔布斯的审核？"
→ 本审计按 Dieter Rams 十诫量化评分（0-30），给出 NEW / REFINE / REDESIGN 判决。
「乔布斯审核」≈ Rams #3 aesthetic + #5 unobtrusive + #8 thorough + #10 as-little-design 的合并检验。

## Reference
- 设计哲学：`~/.claude/references/dev-philosophy.md`
- Base 自身规范：项目 CLAUDE.md「设计规范」节
