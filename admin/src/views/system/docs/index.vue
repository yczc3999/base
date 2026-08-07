<template>
  <div class="docs-page">
    <div class="page-head">
      <h2 class="page-title">组件与设计令牌</h2>
      <p class="page-subtitle">Base Admin 设计系统速查 · 扁平 / 大色块 / 极窄圆角</p>
    </div>

    <!-- ── 设计令牌 ── -->
    <section class="content-card">
      <div class="card-header">
        <span class="card-title">设计令牌（Design Tokens）</span>
        <span class="card-tag">variables.scss</span>
      </div>
      <table class="tokens-table">
        <thead>
          <tr>
            <th style="width: 40px">色块</th>
            <th>令牌</th>
            <th>值</th>
            <th>用途</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="t in tokens" :key="t.name">
            <td>
              <span v-if="t.isColor" class="swatch" :style="{ background: t.value }" />
              <span v-else class="swatch is-text">{{ t.value }}</span>
            </td>
            <td class="mono">{{ t.name }}</td>
            <td class="mono">{{ t.value }}</td>
            <td class="desc">{{ t.desc }}</td>
          </tr>
        </tbody>
      </table>
    </section>

    <!-- ── StatCard ── -->
    <section class="content-card">
      <div class="card-header">
        <span class="card-title">StatCard 统计卡</span>
        <span class="card-tag">components/StatCard</span>
      </div>
      <div class="stat-grid">
        <StatCard
          :icon="Users"
          value="128"
          label="用户总数"
          accent="var(--primary)"
        />
        <StatCard
          :icon="Activity"
          :count="true"
          :value="998"
          label="本月操作（数字滚动）"
          accent="var(--success)"
        />
        <StatCard
          :icon="MessagesSquare"
          value="24"
          label="未读消息"
          accent="var(--warning)"
        />
        <StatCard
          :icon="CircleAlert"
          value="3"
          label="异常任务（可点击）"
          accent="var(--danger)"
          @click="onStatClick"
        />
      </div>
      <pre class="code-block"><code>{{ statCardCode }}</code></pre>
    </section>

    <!-- ── PageShell（未创建 → 用法存根） ── -->
    <section class="content-card">
      <div class="card-header">
        <span class="card-title">PageShell 页面外壳</span>
        <span class="card-tag">待创建</span>
      </div>
      <div class="stub-tip">
        <Info :size="16" />
        PageShell 尚未创建（规划中的页面级容器）。落地后将提供
        <code>title / description / actions</code> 插槽，统一页头、面包屑与操作区。
        当前页面使用下述等价结构：
      </div>
      <pre class="code-block"><code>{{ pageShellCode }}</code></pre>
    </section>

    <!-- ── CrudTable ── -->
    <section class="content-card">
      <div class="card-header">
        <span class="card-title">CrudTable 声明式 CRUD</span>
        <span class="card-tag">components/CrudTable</span>
      </div>
      <div class="card-body">
        <p class="lead">
          后端一行注册 CRUD 后，前端配置 <code>api + columns + formFields</code>
          即得列表 / 搜索 / 新增 / 编辑 / 删除 / 导出。权限点由
          <code>perms</code> 声明，配合按钮权限指令。
        </p>
      </div>
      <pre class="code-block"><code>{{ crudTableCode }}</code></pre>
    </section>

    <!-- ── confirmDialog ── -->
    <section class="content-card">
      <div class="card-header">
        <span class="card-title">confirmDialog 全局确认</span>
        <span class="card-tag">utils/confirm</span>
      </div>
      <div class="card-body">
        <p class="lead">
          命令式全局确认弹窗，签名对齐 ElMessageBox.confirm，确认 resolve(true)、
          取消 reject。按钮色 / 图标 / 色块由 <code>type</code> 驱动。
        </p>
        <div class="demo-actions">
          <el-button type="danger" :icon="Delete" @click="onDeleteDemo">
            删除确认（danger）
          </el-button>
          <el-button :icon="Check" @click="onInfoDemo">
            成功提示（success）
          </el-button>
        </div>
      </div>
      <pre class="code-block"><code>{{ confirmCode }}</code></pre>
    </section>
  </div>
</template>

<script setup lang="ts">
defineOptions({ name: 'SystemDocs' })
import { ElMessage } from 'element-plus/es/components/message/index'
import {
  Users,
  Activity,
  MessagesSquare,
  CircleAlert,
  Info,
  Delete,
  Check,
} from 'lucide-vue-next'
import StatCard from '@/components/StatCard/index.vue'
import { confirmDialog } from '@/utils/confirm'

// ⚠️ 菜单接入：本页为系统组件文档页，未在 DB 写入菜单行。
// 若需在后台侧边栏可见，请在 menus 表添加
//   type=1(菜单) / path=system/docs / template_path=system/docs/index / parent_id=系统管理。
const onStatClick = () => {
  ElMessage.info('点击了异常任务统计卡（StatCard @click）')
}

const onDeleteDemo = async () => {
  try {
    await confirmDialog('确认删除这条记录吗？该操作不可撤销。', '删除确认', {
      type: 'error',
      confirmButtonText: '删除',
    })
    ElMessage.success('已删除（演示）')
  } catch {
    /* 用户取消 */
  }
}

const onInfoDemo = async () => {
  try {
    await confirmDialog('操作已完成，无需进一步确认。', '操作成功', {
      type: 'success',
      showCancel: false,
      confirmButtonText: '知道了',
    })
  } catch {
    /* 纯告知型：仅确定 */
  }
}

const tokens = [
  { name: '--primary', value: '#3B82F6', isColor: true, desc: '主色（Primary）' },
  { name: '--primary-hover', value: '#2563EB', isColor: true, desc: '主色 Hover' },
  { name: '--success', value: '#16A34A', isColor: true, desc: '成功' },
  { name: '--warning', value: '#F59E0B', isColor: true, desc: '警告' },
  { name: '--danger', value: '#EF4444', isColor: true, desc: '危险 / 删除' },
  { name: '--info', value: '#6B7280', isColor: true, desc: '中性信息' },
  { name: '--text-primary', value: '#0F172A', isColor: true, desc: '正文文字' },
  { name: '--text-secondary', value: '#64748B', isColor: true, desc: '次要文字' },
  { name: '--border', value: '#E2E8F0', isColor: true, desc: '分隔边框' },
  { name: '--bg-card', value: '#FFFFFF', isColor: true, desc: '卡片 / 表面' },
  { name: '--bg-page', value: '#F1F5F9', isColor: true, desc: '页面背景' },
  { name: '--radius', value: '2px', isColor: false, desc: '默认圆角' },
  { name: '--radius-sm', value: '1px', isColor: false, desc: '小圆角' },
  { name: '--radius-lg', value: '3px', isColor: false, desc: '大圆角' },
  { name: '--space-xs', value: '4px', isColor: false, desc: '极小间距' },
  { name: '--space-sm', value: '8px', isColor: false, desc: '小间距' },
  { name: '--space-md', value: '12px', isColor: false, desc: '中间距' },
  { name: '--space-base', value: '16px', isColor: false, desc: '基准间距' },
  { name: '--space-lg', value: '20px', isColor: false, desc: '大间距' },
  { name: '--space-xl', value: '24px', isColor: false, desc: '特大间距' },
  { name: '--text-xs', value: '12px', isColor: false, desc: '小字' },
  { name: '--text-sm', value: '13px', isColor: false, desc: '次要说明' },
  { name: '--text-base', value: '14px', isColor: false, desc: '正文' },
  { name: '--text-lg', value: '16px', isColor: false, desc: '标题小' },
  { name: '--text-xl', value: '18px', isColor: false, desc: '标题' },
  { name: '--text-2xl', value: '24px', isColor: false, desc: '大标题 / 数值' },
]

const statCardCode = `<StatCard
  :icon="Users"
  :value="128"
  label="用户总数"
  accent="var(--primary)"
  @click="onClick"   <!-- 可选：绑定后可键盘可达 -->
/>`

const pageShellCode = `<PageShell title="订单管理" description="客户订单列表">
  <!-- 预留插槽：actions / 筛选 / 主内容 -->
  <CrudTable ... />
</PageShell>`

const crudTableCode = `<CrudTable
  api="admin/user"
  :columns="columns"
  :search-fields="searchFields"
  :form-fields="formFields"
  perms="admin:user"
  exportable
/>
<!-- columns / searchFields / formFields 类型见 components/CrudTable/types.ts -->`

const confirmCode = `import { confirmDialog } from '@/utils/confirm'

await confirmDialog('确认删除这条记录吗？', '删除确认', {
  type: 'error',
  confirmButtonText: '删除',
})
// 确认 → true；取消 / 遮罩 / Esc → reject('cancel')`
</script>

<style scoped lang="scss">
.docs-page {
  display: flex;
  flex-direction: column;
  gap: var(--space-base);
}

.page-head {
  margin-bottom: var(--space-sm);
}

.page-title {
  margin: 0;
  font-size: var(--text-2xl);
  font-weight: 700;
  color: var(--text-primary);
}

.page-subtitle {
  margin: var(--space-xs) 0 0;
  font-size: var(--text-sm);
  color: var(--text-secondary);
}

.content-card {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius);
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--space-md) var(--space-lg);
  border-bottom: 1px solid var(--border-light);
}

.card-title {
  font-size: var(--text-lg);
  font-weight: 600;
  color: var(--text-primary);
}

.card-tag {
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  color: var(--text-secondary);
}

.card-body {
  padding: var(--space-lg);
}

.lead {
  margin: 0 0 var(--space-md);
  font-size: var(--text-sm);
  line-height: 1.7;
  color: var(--text-secondary);

  code {
    padding: 1px 6px;
    border-radius: 2px;
    background: var(--bg-input);
    border: 1px solid var(--border-light);
    font-family: var(--font-mono);
    color: var(--text-primary);
  }
}

/* ── 令牌表 ── */
.tokens-table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--text-sm);

  th,
  td {
    padding: var(--space-sm) var(--space-lg);
    text-align: left;
    border-bottom: 1px solid var(--border-light);
  }

  th {
    font-weight: 600;
    color: var(--text-secondary);
    background: var(--bg-table-header);
  }

  tr:last-child td {
    border-bottom: none;
  }

  .desc {
    color: var(--text-secondary);
  }
}

.swatch {
  display: inline-block;
  width: 24px;
  height: 24px;
  border-radius: 2px;
  border: 1px solid var(--border);
  vertical-align: middle;

  &.is-text {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--text-secondary);
    background: var(--bg-input);
  }
}

.mono {
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  color: var(--text-primary);
}

/* ── StatCard 演示 ── */
.stat-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: var(--space-base);
  padding: var(--space-lg);
}

/* ── 代码块 ── */
.code-block {
  margin: 0;
  padding: var(--space-md) var(--space-lg);
  background: var(--bg-input);
  border-top: 1px solid var(--border-light);
  border-radius: 0 0 var(--radius) var(--radius);
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  line-height: 1.7;
  color: var(--text-primary);
  overflow-x: auto;
  white-space: pre;
}

/* ── 存根提示 ── */
.stub-tip {
  display: flex;
  align-items: center;
  gap: var(--space-sm);
  margin: var(--space-lg);
  padding: var(--space-sm) var(--space-md);
  border: 1px solid var(--border);
  border-left: 3px solid var(--primary);
  border-radius: var(--radius);
  font-size: var(--text-sm);
  color: var(--text-secondary);
  line-height: 1.6;

  code {
    font-family: var(--font-mono);
    color: var(--primary);
  }
}

/* ── 演示操作 ── */
.demo-actions {
  display: flex;
  gap: var(--space-sm);
  flex-wrap: wrap;
}
</style>
