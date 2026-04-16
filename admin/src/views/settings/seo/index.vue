<template>
  <div v-loading="loading" class="seo-settings">
    <header class="page-header">
      <h2>SEO 配置</h2>
      <span class="subtitle">跨项目通用；站点 URL + 凭据 + 当前策略 + 进阶质量闸</span>
    </header>

    <!-- 前置提示 -->
    <div class="precheck">
      <span class="pc-icon">ℹ️</span>
      站点名称 / 前端 URL / 建站日期 / 时区 等通用配置在
      <router-link to="/settings/site">站点设置</router-link> 里管理。
      本页只放 SEO 专用的凭据和质量闸。
    </div>

    <!-- 1. 凭据 -->
    <section class="card">
      <div class="card-head">
        <div class="card-num">01</div>
        <div>
          <h3>搜索引擎对接凭据</h3>
          <p>通知搜索引擎更新、获取收录数据。可全部留空，系统会降级运行</p>
        </div>
      </div>
      <el-form label-width="120px" class="form">
        <el-form-item label="IndexNow Key">
          <div class="row-flex">
            <el-input v-model="credsForm.indexnow_key" placeholder="未生成则禁用 IndexNow" />
            <el-button @click="genIndexNowKey">生成 Key</el-button>
            <el-button :disabled="!credsForm.indexnow_key" @click="testIndexNow">测试</el-button>
          </div>
          <div class="hint">
            <b>干什么用</b>：文章发布时立刻通知 <b>Bing / Yandex / Seznam</b>「新页面来爬」，
            比等它们自己发现快几天。<br/>
            <b>注意</b>：Google <b>不</b>参与 IndexNow，Google 只看 sitemap.xml。<br/>
            <b>怎么获取</b>：点「生成」按钮本地生成一个随机 32 位 hex 字符串即可（无需注册），
            系统自动把它暴露在 <code>{{ '{' }}frontend_url{{ '}' }}/{{ '{' }}key{{ '}' }}.txt</code> 路由上。
            <a href="https://www.indexnow.org/documentation" target="_blank" rel="noreferrer">协议文档 ↗</a>
            ｜
            <a href="https://www.bing.com/indexnow" target="_blank" rel="noreferrer">Bing 官方介绍 ↗</a>
          </div>
        </el-form-item>
        <el-form-item label="GSC 凭据">
          <el-input v-model="credsForm.gsc_service_account_json" type="textarea" :rows="4"
            placeholder="Phase 2 才会用；留空不影响主功能" />
          <div class="hint">
            <span class="phase-tag">Phase 2</span>
            <b>干什么用</b>：拉 <b>Google Search Console</b> 的「已收录页面数」。
            系统用它计算"索引健康度" —— 知道 Google 吸收了多少内容，判断是不是被沙盒期压制。<br/>
            <b>怎么获取</b>：
            ① 在
            <a href="https://search.google.com/search-console" target="_blank" rel="noreferrer">Google Search Console ↗</a>
            验证域名所有权 →
            ② 到
            <a href="https://console.cloud.google.com/apis/credentials" target="_blank" rel="noreferrer">GCP Credentials ↗</a>
            创建 service account → 下载 JSON →
            ③ 在 GSC 的"用户和权限"里把 service account 邮箱加为「受限用户」→
            ④ 整段 JSON 粘到这里。<br/>
            <b>不填会怎样</b>：健康度显示"—"，不影响发布流程。
          </div>
        </el-form-item>
        <el-form-item label="Bing API Key">
          <el-input v-model="credsForm.bing_api_key" placeholder="Phase 2 才会用" />
          <div class="hint">
            <span class="phase-tag">Phase 2</span>
            <b>干什么用</b>：GSC 拉不到数据时的备选源，从 <b>Bing Webmaster Tools</b> 获取收录数。<br/>
            <b>怎么获取</b>：登录
            <a href="https://www.bing.com/webmasters" target="_blank" rel="noreferrer">Bing Webmaster Tools ↗</a>
            → 右上角用户名 → "API 访问" → 生成 API Key → 粘到这里。<br/>
            <b>不填会怎样</b>：没 GSC 又没 Bing，健康度始终空。
          </div>
        </el-form-item>
      </el-form>
      <div class="card-foot">
        <el-button type="primary" @click="saveCreds">保存凭据</el-button>
      </div>
    </section>

    <!-- 2. 进阶 质量闸 -->
    <el-collapse class="advanced-wrap">
      <el-collapse-item name="quality">
        <template #title>
          <div class="collapse-title">
            <span class="tag-gear">⚙️</span>
            <b>进阶：质量闸</b>
            <span class="warn-note">默认值有研究依据，慎调</span>
          </div>
        </template>
        <div class="quality-form">
          <div class="gate-intro">
            每篇文章发布前必须通过以下检查。任一不通过就跳过（记录在发布日志里）。
          </div>
          <el-form label-width="160px">
            <el-form-item label="文章最少字数">
              <el-input-number v-model.number="qualityForm.quality_min_content_length" :min="200" :max="3000" />
              <div class="hint">
                正文少于此字数直接跳过。Google 对 500 字以下判定为 "thin content"（低质薄内容），
                安全区 800+。
              </div>
            </el-form-item>
            <el-form-item label="相似度阈值">
              <el-input-number v-model.number="qualityForm.quality_max_similarity_hamming" :min="3" :max="20" />
              <div class="hint">
                新文和库里最近 200 篇的 simhash（64-bit 内容指纹）距离小于此值视为重复，跳过。
                典型 10-12 是"形近实同"区间；调小更严（更多跳过），调大更宽。
              </div>
            </el-form-item>
            <el-form-item label="必须 AI 润色">
              <el-switch v-model="qualityForm.quality_require_ai_processed" />
              <div class="hint">开启后：未经 AI 改写的原始采集文章不准发，防止搜索引擎判"抄袭"降权。</div>
            </el-form-item>
            <el-form-item label="必须封面图">
              <el-switch v-model="qualityForm.quality_require_cover_image" />
              <div class="hint">开启后：没封面图的文章不准发。有封面图 Google 展示更丰富，点击率更高。</div>
            </el-form-item>
            <el-form-item label="同标签 7 天上限">
              <el-input-number v-model.number="qualityForm.quality_max_tag_repeat_7d" :min="1" :max="5" />
              <div class="hint">
                同一个标签（topic）7 天内已发 N 篇后，再有候选文走这个标签就跳过。
                防止"同一主题灌水"被 Google 判 Doorway Pages（门户页）降权。
              </div>
            </el-form-item>
            <el-form-item>
              <el-button type="warning" @click="saveQuality">保存质量闸（将弹窗确认）</el-button>
            </el-form-item>
          </el-form>
        </div>
      </el-collapse-item>
    </el-collapse>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { get, post } from '@/api/request'

const loading = ref(false)

const credsForm = reactive({ indexnow_key: '', gsc_service_account_json: '', bing_api_key: '' })
const qualityForm = reactive({
  quality_min_content_length: 800,
  quality_max_similarity_hamming: 10,
  quality_require_ai_processed: true,
  quality_require_cover_image: false,
  quality_max_tag_repeat_7d: 2,
})

async function load() {
  loading.value = true
  try {
    const all = await get('/admin/setting/get')
    Object.assign(credsForm, all?.seo_creds || {})
    const seo = all?.seo || {}
    qualityForm.quality_min_content_length = parseInt(seo.quality_min_content_length || '800')
    qualityForm.quality_max_similarity_hamming = parseInt(seo.quality_max_similarity_hamming || '10')
    qualityForm.quality_require_ai_processed = seo.quality_require_ai_processed === 'true'
    qualityForm.quality_require_cover_image = seo.quality_require_cover_image === 'true'
    qualityForm.quality_max_tag_repeat_7d = parseInt(seo.quality_max_tag_repeat_7d || '2')
  } finally { loading.value = false }
}

async function saveSection(category: string, data: any) {
  const stringified: Record<string, string> = {}
  for (const [k, v] of Object.entries(data)) {
    stringified[k] = typeof v === 'boolean' ? String(v) : String(v ?? '')
  }
  await post('/admin/setting/set', { [category]: stringified })
}

async function saveCreds() {
  await saveSection('seo_creds', credsForm)
  ElMessage.success('已保存')
}

async function saveQuality() {
  await ElMessageBox.confirm(
    '调整质量闸会影响后续发布判定。文档 §3 有阈值依据。继续？',
    '保存质量闸', { type: 'warning' }
  )
  await saveSection('seo', qualityForm as any)
  ElMessage.success('已保存')
}

function genIndexNowKey() {
  const arr = new Uint8Array(16)
  crypto.getRandomValues(arr)
  credsForm.indexnow_key = Array.from(arr, b => b.toString(16).padStart(2, '0')).join('')
  ElMessage.info('已生成新 key，记得保存')
}

async function testIndexNow() {
  // 从 site 配置读 frontend_url（现在 URL 配置在 /settings/site）
  const all = await get('/admin/setting/get')
  const url = (all?.site?.frontend_url || '').replace(/\/$/, '')
  if (!url) return ElMessage.warning('请先到「站点设置」填前端 URL')
  await saveCreds()
  try {
    const res = await post('/admin/seo/indexnow/test', { urls: [`${url}/`] })
    ElMessage.success(`IndexNow 测试 OK: ${JSON.stringify(res)}`)
  } catch (e: any) {
    ElMessage.error(e?.message || 'IndexNow 测试失败')
  }
}

onMounted(load)
</script>

<style scoped>
.seo-settings {
  padding: 20px;
  max-width: 900px;
  display: flex; flex-direction: column; gap: 20px;
  font-family: 'Inter', 'PingFang SC', 'Microsoft YaHei', sans-serif;
}
.page-header h2 { font-size: 22px; font-weight: 800; color: #0f172a; margin: 0; letter-spacing: -0.02em; }
.subtitle { font-size: 13px; color: #64748b; }

.precheck {
  padding: 12px 16px; background: #eff6ff; border: 1px solid #dbeafe; border-radius: 6px;
  font-size: 13px; color: #1e40af; line-height: 1.6;
}
.precheck .pc-icon { margin-right: 6px; }
.precheck a { color: #2563eb; font-weight: 600; text-decoration: underline; }

.card {
  background: #fff; border: 1px solid #e2e8f0; border-radius: 8px;
  padding: 24px 28px;
}
.card-head {
  display: flex; align-items: flex-start; gap: 16px; margin-bottom: 24px;
  padding-bottom: 20px; border-bottom: 1px solid #f1f5f9;
}
.card-num {
  width: 40px; height: 40px; border-radius: 8px;
  background: #eff6ff; color: #2563eb;
  display: flex; align-items: center; justify-content: center;
  font-size: 14px; font-weight: 900; letter-spacing: -0.02em;
  flex-shrink: 0;
}
.card-num.muted { background: #f1f5f9; color: #64748b; }
.card-head h3 { font-size: 16px; font-weight: 700; color: #0f172a; margin: 0; }
.card-head p { font-size: 13px; color: #64748b; margin: 4px 0 0; }
.card-foot { margin-top: 12px; padding-top: 20px; border-top: 1px solid #f1f5f9; }

.form { margin-top: -4px; }
.hint { font-size: 12px; color: #64748b; margin-top: 4px; line-height: 1.7; }
.hint code { background: #f1f5f9; padding: 1px 6px; border-radius: 3px; font-size: 11px; color: #334155; }
.hint a {
  color: #2563eb; text-decoration: none;
  border-bottom: 1px dashed #93c5fd; padding-bottom: 1px;
}
.hint a:hover { color: #1d4ed8; border-bottom-style: solid; }
.row-flex { display: flex; gap: 8px; width: 100%; }
.row-flex :deep(.el-input) { flex: 1; }

.phase-tag {
  display: inline-block; padding: 1px 8px; margin-right: 6px;
  background: #fef3c7; color: #d97706; border-radius: 3px;
  font-size: 10px; font-weight: 700; letter-spacing: 0.05em;
}

/* Gate intro */
.gate-intro {
  padding: 12px 16px; margin-bottom: 16px;
  background: #fffbeb; border-left: 3px solid #f59e0b; border-radius: 4px;
  font-size: 13px; color: #92400e; line-height: 1.6;
}

/* Advanced collapse */
.advanced-wrap {
  background: #fff; border: 1px solid #e2e8f0; border-radius: 8px;
  padding: 4px 24px;
}
.advanced-wrap :deep(.el-collapse-item__header) { font-size: 15px; }
.advanced-wrap :deep(.el-collapse-item__wrap) { border: none; }
.collapse-title { display: flex; align-items: center; gap: 8px; }
.tag-gear { font-size: 16px; }
.warn-note {
  margin-left: 8px; padding: 2px 8px; border-radius: 3px;
  background: #fffbeb; color: #d97706; font-size: 11px; font-weight: 600;
}
.quality-form { padding: 12px 0 20px; }
</style>
