<template>
  <div v-loading="loading" class="seo-dashboard">
    <!-- 主开关（持久化，重启不丢）-->
    <section class="master-switch" :class="{ on: enabled, off: !enabled }">
      <div class="ms-left">
        <div class="ms-icon">
          <Zap v-if="enabled" :size="32" :stroke-width="2" />
          <Pause v-else :size="32" :stroke-width="2" />
        </div>
        <div>
          <h2>自动发布 {{ enabled ? '已开启' : '已关闭' }}</h2>
          <p>{{ enabled ? 'worker 按节奏自动采集 / 排期 / 发布' : '所有 worker 任务暂停；点开关启动' }}</p>
        </div>
      </div>
      <el-switch
v-model="enabled" :loading="toggling" size="large" active-text="开"
        inactive-text="关" inline-prompt @change="onToggle" />
    </section>

    <!-- 1. 阶段状态卡 -->
    <section class="phase-card">
      <div class="phase-left">
        <div class="phase-emoji">
          <component :is="phaseIcon[currentPhase]" :size="34" :stroke-width="1.8" />
        </div>
        <div class="phase-text">
          <h2>{{ phaseName[currentPhase] }}</h2>
          <p>{{ phaseSubtitle }}</p>
        </div>
      </div>
      <div class="phase-actions">
        <el-button :icon="Refresh" :loading="recomputing" class="btn-ghost" @click="recompute">重算阶段</el-button>
      </div>
    </section>

    <!-- 2. 三个配额卡 -->
    <section class="quota-row">
      <div class="quota-card">
        <div>
          <span class="quota-label">今日发布</span>
          <div class="quota-num">{{ data?.usage?.today ?? 0 }} / {{ quotaDaily }}</div>
        </div>
        <div class="quota-progress">
          <div
class="qp-bar"
               :style="{ width: quotaDaily > 0 ? Math.min((data?.usage?.today ?? 0) / quotaDaily * 100, 100) + '%' : '0%' }"></div>
        </div>
      </div>

      <div class="quota-card">
        <div>
          <span class="quota-label">本周发布</span>
          <div class="quota-num">{{ data?.usage?.this_week ?? 0 }} / {{ quotaWeekly }}</div>
        </div>
        <div class="quota-progress">
          <div
class="qp-bar"
               :style="{ width: quotaWeekly > 0 ? Math.min((data?.usage?.this_week ?? 0) / quotaWeekly * 100, 100) + '%' : '0%' }"></div>
        </div>
      </div>

      <div class="quota-card">
        <div>
          <span class="quota-label">搜索引擎收录健康度</span>
          <div class="quota-num-row">
            <span class="quota-num">{{ healthDisplay }}</span>
            <span v-if="health !== null" class="health-pill" :class="`hp-${healthClass}`">
              <span class="health-dot"></span>{{ healthLabel }}
            </span>
          </div>
        </div>
        <div class="health-bar">
          <div
class="health-bar-fill" :class="`hp-${healthClass}`"
               :style="{ width: healthPercent + '%' }"></div>
        </div>
      </div>
    </section>

    <!-- 3. 两列：草稿池 + 最近活动 -->
    <section class="col-row">
      <div class="col-card">
        <div class="col-head">
          <h3>文章池</h3>
        </div>
        <div class="col-body stats-grid">
          <div class="stat-cell">
            <p class="cell-label">草稿</p>
            <span class="cell-num">{{ data?.article_stats?.drafts ?? 0 }}</span>
          </div>
          <div class="stat-cell">
            <p class="cell-label">排队中</p>
            <span class="cell-num primary">{{ data?.article_stats?.scheduled ?? 0 }}</span>
          </div>
          <div class="stat-cell">
            <p class="cell-label">已发</p>
            <span class="cell-num success">{{ data?.article_stats?.published ?? 0 }}</span>
          </div>
          <div class="stat-cell" :class="{ highlight: (data?.article_stats?.failed ?? 0) > 0 }">
            <p class="cell-label">失败</p>
            <span class="cell-num" :class="(data?.article_stats?.failed ?? 0) > 0 ? 'warning' : 'muted'">
              {{ data?.article_stats?.failed ?? 0 }}
            </span>
          </div>
          <div class="stat-cell">
            <p class="cell-label">已删</p>
            <span class="cell-num muted">{{ data?.article_stats?.deleted ?? 0 }}</span>
          </div>
        </div>
        <div class="col-foot">
          <span>草稿池 → 排期 → 发布</span>
          <span v-if="data?.article_stats?.next_due" class="next-due">
            下一篇：{{ formatTime(data.article_stats.next_due) }}
          </span>
        </div>
      </div>

      <div class="col-card">
        <div class="col-head">
          <h3>最近活动</h3>
        </div>
        <div v-if="!data?.recent_activity?.length" class="empty-state">
          <div class="empty-icon">·</div>
          <p class="empty-title">还没有活动</p>
          <p class="empty-subtitle">点「立即跑全链路」启动 worker。</p>
        </div>
        <div v-else class="activity-list">
          <div v-for="a in data.recent_activity" :key="a.id" class="activity-item">
            <span class="ai-time">{{ shortTime(a.created_at) }}</span>
            <span class="ai-icon" :class="`ai-${a.level}`">{{ activityIcon(a.action, a.level) }}</span>
            <span class="ai-msg">{{ a.msg }}</span>
          </div>
        </div>
      </div>
    </section>

    <!-- 完整策略指标（原 settings 搬来）-->
    <section class="metrics-card">
      <div class="mc-head">
        <h3>策略详情</h3>
        <span class="mc-sub">系统按阶段自动算出，这里是完整依据</span>
      </div>
      <div class="mc-grid">
        <div class="metric">
          <span class="m-label">建站天数</span>
          <span class="m-value">{{ data?.phase?.age_days ?? 0 }}</span>
        </div>
        <div class="metric">
          <span class="m-label">已发布</span>
          <span class="m-value">{{ data?.phase?.published_cnt ?? 0 }}</span>
        </div>
        <div class="metric">
          <span class="m-label">Google 收录</span>
          <span class="m-value">{{ data?.phase?.indexed_cnt ?? '—' }}</span>
        </div>
        <div class="metric">
          <span class="m-label">今日上限</span>
          <span class="m-value primary">{{ quotaDaily }}</span>
        </div>
        <div class="metric">
          <span class="m-label">本周上限</span>
          <span class="m-value primary">{{ quotaWeekly }}</span>
        </div>
        <div class="metric">
          <span class="m-label">上次计算</span>
          <span class="m-time">{{ data?.phase?.last_check || '—' }}</span>
        </div>
      </div>
    </section>

    <!-- 4. Sitemap 状态条 -->
    <section class="status-bar">
      <div class="status-left">
        <span class="status-label">sitemap：</span>
        <span class="chip">{{ sitemapInfo.files }} 分片</span>
        <span class="chip">{{ sitemapInfo.urls }} URL</span>
        <span class="chip chip-info">{{ sitemapInfo.freshness }}</span>
      </div>
      <div class="status-right">
        <div class="status-chip" :class="data?.phase?.indexed_cnt ? 'on' : 'off'">
          <span class="dot"></span>
          GSC：{{ data?.phase?.indexed_cnt ? '已连接' : '未连接' }}
        </div>
        <div class="status-chip" :class="hasIndexNowKey ? 'on' : 'off'">
          <span class="dot"></span>
          IndexNow：{{ hasIndexNowKey ? '已启用' : '未启用' }}
        </div>
      </div>
    </section>

    <!-- 5. 紧急禁发 -->
    <section class="emergency-panel" :class="{ active: data?.kill_switch?.on }">
      <div class="eh">
        
        <h3>紧急禁发</h3>
        <span v-if="data?.kill_switch?.on" class="ks-badge">
          已禁发{{ data.kill_switch.until ? '（截止 ' + formatTime(data.kill_switch.until) + '）' : '' }}
        </span>
      </div>
      <div v-if="!data?.kill_switch?.on" class="e-btns">
        <button class="eb eb-soft" @click="setKillSwitch(true, '24h')">禁发 24 小时</button>
        <button class="eb eb-hard" @click="setKillSwitch(true, 'week')">禁发本周</button>
        <button class="eb eb-danger" @click="setKillSwitch(true, 'forever')">永久禁发</button>
      </div>
      <button v-else class="eb eb-recover" @click="setKillSwitch(false, null)">立即恢复</button>
      <p class="e-hint">启用紧急禁发将立即停止所有排期中的自动发布任务。需要手动解除。</p>
    </section>

    <!-- 入口链接 -->
    <section class="links-row">
      <router-link to="/seo/log">→ 发布日志</router-link>
      <router-link to="/seo/sitemap">→ sitemap 管理</router-link>
      <router-link to="/settings/seo">→ SEO 配置</router-link>
    </section>

    <!-- 调试工具（折叠，平时收起）-->
    <el-collapse class="debug-wrap">
      <el-collapse-item name="debug">
        <template #title>
          <span class="debug-title">调试工具</span>
          <span class="debug-hint">手动触发 / 跳过等待</span>
        </template>
        <div class="debug-grid">
          <div class="debug-item">
            <div class="di-text">
              <b>立即跑一次完整链路</b>
              <p>pipeline 补货 → scheduler 排期 → publisher 发到点。<br>
                 用于 dev 验证 / 应急加速。生产环境通常不用，信任 worker 自动节奏。</p>
            </div>
            <el-button :icon="VideoPlay" :loading="running" type="primary" plain @click="runNow">跑一次</el-button>
          </div>
        </div>
      </el-collapse-item>
    </el-collapse>
  </div>
</template>

<script setup lang="ts">
defineOptions({ name: 'SeoDashboard' })
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus/es/components/message/index'
import { confirmDialog } from '@/utils/confirm'
import { Refresh, VideoPlay } from '@element-plus/icons-vue'
import { Snowflake, Sprout, TrendingUp, Landmark, Zap, Pause } from 'lucide-vue-next'
import { get, post } from '@/api/request'

const data = ref<any>(null)
const loading = ref(false)
const recomputing = ref(false)
const running = ref(false)
const enabled = ref(false)
const toggling = ref(false)

const phaseName: Record<string, string> = { cold: '冷启动', new: '新站', growing: '成长期', stable: '稳定期' }
const phaseIcon: Record<string, any> = {
  cold: Snowflake, new: Sprout, growing: TrendingUp, stable: Landmark,
}

const currentPhase = computed(() => data.value?.phase?.phase || 'cold')
const phaseSubtitle = computed(() => data.value?.phase?.reason || '加载中...')
const quotaDaily = computed(() => data.value?.phase?.quota?.daily ?? 0)
const quotaWeekly = computed(() => data.value?.phase?.quota?.weekly ?? 0)
const health = computed<number | null>(() => {
  const h = data.value?.phase?.health_score
  return h === null || h === undefined || h === '' ? null : Number(h)
})
const healthDisplay = computed(() => health.value === null ? '—' : health.value.toFixed(2))
const healthPercent = computed(() => health.value === null ? 0 : Math.min(health.value * 100, 100))
const healthClass = computed(() => {
  const h = health.value
  if (h === null) return 'none'
  if (h >= 1.0) return 'excellent'
  if (h >= 0.7) return 'good'
  if (h >= 0.4) return 'watch'
  return 'bad'
})
const healthLabel = computed(() => ({
  excellent: '优秀', good: '健康', watch: '观察', bad: '异常', none: '—'
}[healthClass.value]))

const hasIndexNowKey = computed(() => !!data.value?.phase?.quota?.indexnow)
const sitemapInfo = computed(() => {
  const s = data.value?.sitemap
  if (!s || !s.files) return { files: '—', urls: '—', freshness: '未生成' }
  const mtime = s.mtime ? new Date(s.mtime * 1000) : null
  return {
    files: `${s.files} 分片`,
    urls: `${s.urls.toLocaleString()} URL`,
    freshness: mtime ? relativeTime(mtime) : '未知',
  }
})

function relativeTime(d: Date): string {
  const diff = (Date.now() - d.getTime()) / 1000
  if (diff < 60) return '刚刚刷新'
  if (diff < 3600) return `${Math.round(diff / 60)} 分钟前刷新`
  if (diff < 86400) return `${Math.round(diff / 3600)} 小时前刷新`
  return `${Math.round(diff / 86400)} 天前刷新`
}

async function load() {
  loading.value = true
  try {
    data.value = await get('/admin/seo/dashboard')
    // enabled 字段从 settings 拉，dashboard endpoint 没返；异步加载
    const settings = await get('/admin/setting/get')
    enabled.value = (settings?.seo?.enabled || 'false') === 'true'
  } finally { loading.value = false }
}

async function onToggle(val: any) {
  const v = val as boolean
  toggling.value = true
  try {
    if (!v) {
      await confirmDialog(
        '关闭后所有 worker 任务（采集 / 排期 / 发布）都会停止，已排队的文章不会发出。',
        '关闭自动发布', { type: 'warning' }
      )
    }
    await post('/admin/seo/toggle', { enabled: v })
    ElMessage.success(v ? '自动发布已开启' : '自动发布已关闭')
    await load()
  } catch {
    enabled.value = !v  // 回滚
  } finally { toggling.value = false }
}

async function recompute() {
  recomputing.value = true
  try {
    await post('/admin/seo/phase/recompute')
    ElMessage.success('阶段已重算')
    await load()
  } finally { recomputing.value = false }
}

async function runNow() {
  running.value = true
  try {
    const res = await post('/admin/seo/run-now')
    const log = res?.log || []
    const pub = log.find((x: any) => x.step === 'publisher')
    const sched = log.find((x: any) => x.step === 'scheduler')
    ElMessage.success(
      `全链路已跑完：scheduled ${sched?.scheduled ?? 0}，published ${pub?.published ?? 0}，` +
      `skipped ${pub?.skipped ?? 0}，errored ${pub?.errored ?? 0}`,
    )
    await load()
  } catch {
    ElMessage.error('运行失败')
  } finally { running.value = false }
}

async function setKillSwitch(on: boolean, duration: string | null) {
  if (on) {
    await confirmDialog(
      `确认 ${duration === 'forever' ? '永久' : duration === 'week' ? '本周' : '24 小时'} 禁发？`,
      '紧急禁发', { type: 'warning' }
    )
  }
  await post('/admin/seo/kill-switch', { on, duration })
  ElMessage.success(on ? '已禁发' : '已恢复')
  await load()
}

function formatTime(t: any): string {
  if (!t) return ''
  return new Date(t).toLocaleString('zh-CN', { hour12: false })
}

function shortTime(t: any): string {
  if (!t) return ''
  const d = new Date(t)
  const diff = (Date.now() - d.getTime()) / 1000
  if (diff < 60) return '刚刚'
  if (diff < 3600) return `${Math.round(diff / 60)} 分钟前`
  if (diff < 86400) return d.toTimeString().slice(0, 5)
  return `${d.getMonth() + 1}/${d.getDate()}`
}

function activityIcon(action: string, level: string): string {
  if (level === 'error') return '✗'
  if (level === 'warn') return '⚠'
  if (action === 'publish') return '✓'
  if (action === 'collect') return '+'
  if (action === 'sitemap_rebuild') return '⟲'
  if (action === 'indexnow') return '↗'
  if (action === 'kill_switch') return '⏸'
  if (action === 'phase_change') return '↻'
  return '·'
}

onMounted(load)
</script>

<style scoped>
.seo-dashboard {
  padding: 32px;
  max-width: 1280px;
  margin: 0 auto;
  display: flex; flex-direction: column; gap: 24px;
  font-family: 'Inter', 'PingFang SC', 'Microsoft YaHei', sans-serif;
  color: #0f172a;
}

/* 通用卡片 */
.phase-card, .quota-card, .col-card, .status-bar, .emergency-panel {
  background: #fff;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
}

/* 主开关：突出显示 */
.master-switch {
  display: flex; align-items: center; justify-content: space-between;
  padding: 24px; border-radius: 8px; border: 2px solid;
}
.master-switch.on {
  background: linear-gradient(135deg, #f0fdf4, #fff);
  border-color: #86efac;
}
.master-switch.off {
  background: linear-gradient(135deg, #fef3c7, #fff);
  border-color: #fcd34d;
}
.ms-left { display: flex; align-items: center; gap: 18px; }
.ms-icon { font-size: 36px; }
.master-switch h2 {
  font-size: 19px; font-weight: 800; margin: 0; letter-spacing: -0.02em;
}
.master-switch.on h2 { color: #15803d; }
.master-switch.off h2 { color: #b45309; }
.master-switch p { font-size: 13px; color: #64748b; margin: 4px 0 0; }

/* 1. Phase card */
.phase-card {
  display: flex; align-items: center; justify-content: space-between;
  padding: 24px;
}
.phase-left { display: flex; align-items: center; gap: 24px; }
.phase-emoji {
  width: 64px; height: 64px; border-radius: 8px;
  background: #eff6ff; display: flex; align-items: center; justify-content: center;
  font-size: 32px;
}
.phase-text h2 { font-size: 24px; font-weight: 800; letter-spacing: -0.02em; margin: 0; }
.phase-text p { color: #64748b; margin: 4px 0 0; font-size: 14px; }
.phase-actions { display: flex; gap: 8px; }
:deep(.btn-ghost) { border-color: #e2e8f0; color: #64748b; font-weight: 600; background: #fff; }
:deep(.btn-ghost:hover) { background: #f8fafc; border-color: #cbd5e1; color: #334155; }

/* 2. Quota row */
.quota-row {
  display: grid; grid-template-columns: repeat(3, 1fr); gap: 24px;
}
.quota-card {
  padding: 20px; display: flex; flex-direction: column; justify-content: space-between;
  min-height: 120px;
}
.quota-label {
  font-size: 11px; font-weight: 700; color: #94a3b8;
  text-transform: uppercase; letter-spacing: 0.15em;
}
.quota-num { font-size: 30px; font-weight: 900; color: #0f172a; margin-top: 4px; line-height: 1.1; }
.quota-num-row { display: flex; align-items: baseline; gap: 8px; margin-top: 4px; }
/* 真实进度条（替代装饰 bar chart）*/
.quota-progress {
  width: 100%; height: 6px; background: #f1f5f9; border-radius: 3px;
  margin-top: 16px; overflow: hidden;
}
.qp-bar { height: 100%; background: #3b82f6; transition: width 0.3s; }

/* Health pill + bar */
.health-pill {
  display: inline-flex; align-items: center; gap: 4px;
  padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 700;
}
.health-dot { width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
.hp-excellent { background: #dcfce7; color: #15803d; }
.hp-good { background: #dcfce7; color: #15803d; }
.hp-watch { background: #fef3c7; color: #d97706; }
.hp-bad { background: #fee2e2; color: #b91c1c; }
.hp-none { background: #f1f5f9; color: #64748b; }
.health-bar {
  width: 100%; height: 6px; background: #f1f5f9; border-radius: 3px;
  margin-top: 16px; overflow: hidden;
}
.health-bar-fill { height: 100%; transition: width 0.3s; }
.health-bar-fill.hp-excellent, .health-bar-fill.hp-good { background: #22c55e; }
.health-bar-fill.hp-watch { background: #f59e0b; }
.health-bar-fill.hp-bad { background: #ef4444; }
.health-bar-fill.hp-none { background: #cbd5e1; }

/* 3. Col cards */
.col-row { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }
.col-card { display: flex; flex-direction: column; overflow: hidden; }
.col-head {
  padding: 16px 24px; border-bottom: 1px solid #f1f5f9;
}
.col-head h3 { font-size: 15px; font-weight: 700; color: #0f172a; margin: 0; }
.col-body { padding: 24px; flex: 1; }
.col-foot {
  padding: 12px 24px; background: #f8fafc; border-top: 1px solid #f1f5f9;
  display: flex; justify-content: space-between; align-items: center;
  font-size: 12px;
}
.col-foot span:first-child { color: #64748b; }
.next-due { color: #2563eb; font-weight: 700; }

/* Stats grid */
.stats-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; }
.stat-cell {
  padding: 16px; border: 1px solid #f1f5f9; border-radius: 4px;
  background: #f8fafc;
}
.stat-cell.highlight { background: #fffbeb; border-color: #fef3c7; }
.cell-label {
  font-size: 10px; font-weight: 700; color: #94a3b8;
  text-transform: uppercase; letter-spacing: 0.1em; margin: 0 0 4px;
}
.stat-cell.highlight .cell-label { color: #d97706; }
.cell-num { font-size: 22px; font-weight: 900; color: #0f172a; }
.cell-num.primary { color: #2563eb; }
.cell-num.muted { color: #94a3b8; }
.cell-num.warning { color: #d97706; }
.cell-num.success { color: #16a34a; }

/* Activity list */
.activity-list { padding: 8px 0; max-height: 360px; overflow-y: auto; }
.activity-item {
  display: grid; grid-template-columns: 60px 24px 1fr; gap: 8px; align-items: center;
  padding: 6px 24px; font-size: 12px; line-height: 1.5;
  border-bottom: 1px dashed #f1f5f9;
}
.activity-item:last-child { border-bottom: none; }
.activity-item:hover { background: #f8fafc; }
.ai-time { color: #94a3b8; font-family: ui-monospace, monospace; font-size: 11px; }
.ai-icon {
  width: 20px; height: 20px; border-radius: 50%;
  display: inline-flex; align-items: center; justify-content: center;
  font-size: 12px; font-weight: bold;
}
.ai-icon.ai-info { background: #eff6ff; color: #2563eb; }
.ai-icon.ai-warn { background: #fffbeb; color: #d97706; }
.ai-icon.ai-error { background: #fef2f2; color: #dc2626; }
.ai-msg { color: #334155; }

/* Empty state */
.empty-state {
  flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center;
  padding: 48px; text-align: center;
}
.empty-icon {
  width: 64px; height: 64px; border-radius: 50%; background: #f0fdf4;
  color: #22c55e; font-size: 28px; font-weight: bold;
  display: flex; align-items: center; justify-content: center;
  margin-bottom: 16px;
}
.empty-title { font-size: 16px; font-weight: 700; color: #0f172a; margin: 0; }
.empty-subtitle { font-size: 13px; color: #64748b; margin: 4px 0 0; }

/* Error list */
.error-list { padding: 16px 24px; max-height: 240px; overflow-y: auto; font-size: 12px; }
.error-item { padding: 6px 0; border-bottom: 1px dashed #f1f5f9; line-height: 1.6; }
.err-time { color: #64748b; margin-right: 6px; font-family: ui-monospace, monospace; }
.err-action { color: #dc2626; margin-right: 6px; font-weight: 600; }

/* 4. Status bar */
.status-bar {
  padding: 16px 20px;
  display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 16px;
}
.status-left { display: flex; align-items: center; gap: 16px; font-size: 13px; }
.status-label { font-weight: 500; color: #334155; }
.chip {
  padding: 4px 12px; border-radius: 999px;
  background: #f1f5f9; color: #475569; font-size: 11px; font-weight: 700;
}
.chip-info { background: #eff6ff; color: #2563eb; }
.status-right { display: flex; gap: 16px; }
.status-chip {
  display: flex; align-items: center; gap: 6px;
  padding: 4px 12px; border-radius: 999px; border: 1px solid;
  font-size: 12px; font-weight: 700;
}
.status-chip.on { background: #f0fdf4; color: #15803d; border-color: #bbf7d0; }
.status-chip.off { background: #f1f5f9; color: #64748b; border-color: #e2e8f0; }
.status-chip .dot { width: 6px; height: 6px; border-radius: 50%; background: currentColor; }

/* 5. Emergency */
.emergency-panel {
  background: #fff7ed; border-color: #fed7aa; padding: 24px;
}
.emergency-panel.active { background: #fef2f2; border-color: #fecaca; }
.eh { display: flex; align-items: center; gap: 12px; margin-bottom: 20px; }
.eh-icon { font-size: 20px; }
.eh h3 { font-size: 17px; font-weight: 900; color: #7c2d12; margin: 0; letter-spacing: -0.01em; }
.ks-badge {
  margin-left: auto; padding: 4px 10px; border-radius: 4px;
  background: #fee2e2; color: #991b1b; font-size: 12px; font-weight: 700;
}
.e-btns { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; }
.eb {
  padding: 12px 24px; border-radius: 6px; border: 2px solid;
  font-weight: 700; font-size: 14px; cursor: pointer; transition: all 0.15s;
}
.eb-soft { background: #fff; border-color: #fb923c; color: #c2410c; }
.eb-soft:hover { background: #fb923c; color: #fff; }
.eb-hard { background: #fff; border-color: #ea580c; color: #9a3412; }
.eb-hard:hover { background: #ea580c; color: #fff; }
.eb-danger { background: #dc2626; border-color: #dc2626; color: #fff; font-weight: 900; }
.eb-danger:hover { background: #b91c1c; }
.eb-recover {
  background: #16a34a; border: 2px solid #16a34a; color: #fff; font-weight: 700;
  padding: 10px 20px; border-radius: 6px; cursor: pointer;
}
.eb-recover:hover { background: #15803d; }
.e-hint { margin: 16px 0 0; font-size: 12px; color: #c2410c; font-weight: 500; }

/* Metrics card */
.metrics-card {
  background: #fff; border: 1px solid #e2e8f0; border-radius: 8px; padding: 20px 24px;
}
.mc-head { display: flex; align-items: baseline; gap: 12px; margin-bottom: 16px; }
.mc-head h3 { font-size: 14px; font-weight: 700; color: #0f172a; margin: 0; }
.mc-sub { font-size: 12px; color: #64748b; }
.mc-grid { display: grid; grid-template-columns: repeat(6, 1fr); gap: 12px; }
.metric {
  padding: 12px 14px; background: #f8fafc; border-radius: 6px; border: 1px solid #f1f5f9;
}
.m-label {
  display: block; font-size: 10px; font-weight: 700; color: #94a3b8;
  text-transform: uppercase; letter-spacing: 0.15em; margin-bottom: 4px;
}
.m-value { font-size: 20px; font-weight: 900; color: #0f172a; }
.m-value.primary { color: #2563eb; }
.m-time { font-size: 11px; color: #64748b; font-family: ui-monospace, monospace; }

@media (max-width: 1100px) {
  .mc-grid { grid-template-columns: repeat(3, 1fr); }
}

/* Links */
.links-row { display: flex; gap: 24px; flex-wrap: wrap; padding: 8px 4px; }
.links-row a {
  color: #2563eb; font-size: 13px; font-weight: 500; text-decoration: none;
  transition: color 0.15s;
}
.links-row a:hover { color: #1d4ed8; text-decoration: underline; }

/* 调试工具（折叠区低视觉权重）*/
.debug-wrap {
  border: 1px dashed #cbd5e1; border-radius: 8px; padding: 0 16px;
  background: #f8fafc;
}
.debug-wrap :deep(.el-collapse-item__header) {
  background: transparent; border: none; font-size: 13px; color: #64748b;
}
.debug-wrap :deep(.el-collapse-item__wrap) { background: transparent; border: none; }
.debug-title { font-weight: 600; color: #475569; }
.debug-hint { margin-left: 12px; font-size: 11px; color: #94a3b8; font-weight: 400; }
.debug-grid { padding: 8px 0 16px; display: flex; flex-direction: column; gap: 12px; }
.debug-item {
  display: flex; align-items: center; justify-content: space-between; gap: 16px;
  padding: 12px 16px; background: #fff; border: 1px solid #e2e8f0; border-radius: 6px;
}
.di-text b { font-size: 13px; color: #0f172a; }
.di-text p { font-size: 12px; color: #64748b; margin: 4px 0 0; line-height: 1.6; }
</style>
