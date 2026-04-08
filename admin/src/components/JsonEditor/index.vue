<template>
  <div class="je">
    <!-- 模式切换 -->
    <div class="je-header">
      <div class="je-tabs">
        <button
          type="button"
          class="je-tab"
          :class="{ active: mode === 'visual' }"
          @click="switchMode('visual')"
        >键值对</button>
        <button
          type="button"
          class="je-tab"
          :class="{ active: mode === 'raw' }"
          @click="switchMode('raw')"
        >JSON</button>
      </div>
      <span v-if="mode === 'raw' && rawError" class="je-error">{{ rawError }}</span>
      <span v-if="mode === 'raw' && !rawError && rawText.trim()" class="je-valid">Valid</span>
    </div>

    <!-- 可视化模式 -->
    <div v-if="mode === 'visual'" class="je-visual">
      <div v-if="!pairs.length" class="je-empty" @click="addPair">
        <span class="je-empty-icon">+</span>
        <span>点击添加配置项</span>
      </div>
      <template v-else>
        <div v-for="(pair, i) in pairs" :key="i" class="je-row">
          <input
            class="je-input je-key"
            v-model="pair.key"
            placeholder="key"
            spellcheck="false"
            @input="emitFromPairs"
            @keydown.enter.prevent="handleKeyEnter(i)"
          />
          <span class="je-sep">=</span>
          <input
            :ref="el => setValueRef(el, i)"
            class="je-input je-value"
            v-model="pair.value"
            placeholder="value"
            spellcheck="false"
            @input="emitFromPairs"
            @keydown.enter.prevent="addPairAfter(i)"
          />
          <button type="button" class="je-row-btn je-row-del" title="删除" @click="removePair(i)">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
          </button>
        </div>
        <button type="button" class="je-add" @click="addPair">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
          添加
        </button>
      </template>
    </div>

    <!-- 原始 JSON 模式 -->
    <div v-else class="je-raw">
      <div class="je-raw-wrap">
        <div class="je-lines" ref="linesRef">
          <div v-for="n in lineCount" :key="n" class="je-ln">{{ n }}</div>
        </div>
        <textarea
          ref="textareaRef"
          class="je-code"
          v-model="rawText"
          :placeholder="placeholder || '{}'"
          spellcheck="false"
          @input="handleRawInput"
          @scroll="syncScroll"
          @keydown.tab.prevent="insertTab"
        />
      </div>
      <div class="je-raw-actions">
        <button type="button" class="je-btn" @click="formatRaw">Format</button>
        <button type="button" class="je-btn" @click="minifyRaw">Minify</button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
const props = defineProps<{
  modelValue?: string | Record<string, any> | null
  placeholder?: string
  disabled?: boolean
}>()

const emit = defineEmits<{
  'update:modelValue': [value: string]
}>()

// ── 状态 ──
const mode = ref<'visual' | 'raw'>('visual')
const pairs = ref<{ key: string; value: string }[]>([])
const rawText = ref('')
const rawError = ref('')
const textareaRef = ref<HTMLTextAreaElement>()
const linesRef = ref<HTMLDivElement>()
const valueRefs: Record<number, HTMLInputElement> = {}

function setValueRef(el: any, i: number) {
  if (el) valueRefs[i] = el
}

const lineCount = computed(() => Math.max((rawText.value || '').split('\n').length, 5))

// ── 初始化：外部值 → 内部状态 ──
function parseValue(val: any): Record<string, any> {
  if (!val) return {}
  if (typeof val === 'object') return { ...val }
  if (typeof val === 'string') {
    try { return JSON.parse(val) } catch { return {} }
  }
  return {}
}

function syncFromProp() {
  const obj = parseValue(props.modelValue)
  pairs.value = Object.entries(obj).map(([key, value]) => ({
    key,
    value: typeof value === 'object' ? JSON.stringify(value) : String(value ?? ''),
  }))
  rawText.value = Object.keys(obj).length ? JSON.stringify(obj, null, 2) : ''
}

syncFromProp()

watch(() => props.modelValue, (newVal) => {
  // 只在外部传入 object 时同步（避免自己 emit 后死循环）
  if (typeof newVal === 'object' && newVal !== null) {
    syncFromProp()
  }
})

// ── 可视化 → emit ──
function pairsToObj(): Record<string, any> {
  const obj: Record<string, any> = {}
  for (const p of pairs.value) {
    const k = p.key.trim()
    if (!k) continue
    // 尝试解析为 JSON 值（数字、布尔、数组、对象）
    const raw = p.value.trim()
    if (raw === '') { obj[k] = ''; continue }
    if (raw === 'true') { obj[k] = true; continue }
    if (raw === 'false') { obj[k] = false; continue }
    if (/^-?\d+(\.\d+)?$/.test(raw)) { obj[k] = Number(raw); continue }
    try {
      const parsed = JSON.parse(raw)
      if (typeof parsed === 'object') { obj[k] = parsed; continue }
    } catch { /* not JSON */ }
    obj[k] = raw
  }
  return obj
}

function emitFromPairs() {
  const obj = pairsToObj()
  const str = Object.keys(obj).length ? JSON.stringify(obj, null, 2) : ''
  rawText.value = str
  emit('update:modelValue', str)
}

// ── 可视化操作 ──
function addPair() {
  pairs.value.push({ key: '', value: '' })
  nextTick(() => {
    // focus 新行的 key input
    const rows = document.querySelectorAll('.je-key')
    const last = rows[rows.length - 1] as HTMLInputElement
    last?.focus()
  })
}

function addPairAfter(i: number) {
  pairs.value.splice(i + 1, 0, { key: '', value: '' })
  nextTick(() => {
    const rows = document.querySelectorAll('.je-key')
    const target = rows[i + 1] as HTMLInputElement
    target?.focus()
  })
}

function removePair(i: number) {
  pairs.value.splice(i, 1)
  emitFromPairs()
}

function handleKeyEnter(i: number) {
  // key 输入回车 → 跳到 value
  valueRefs[i]?.focus()
}

// ── 模式切换 ──
function switchMode(m: 'visual' | 'raw') {
  if (m === mode.value) return
  if (m === 'raw') {
    // visual → raw：同步最新
    const obj = pairsToObj()
    rawText.value = Object.keys(obj).length ? JSON.stringify(obj, null, 2) : ''
    rawError.value = ''
  } else {
    // raw → visual：解析 raw
    if (rawError.value) return // 有错误不让切
    try {
      const obj = rawText.value.trim() ? JSON.parse(rawText.value) : {}
      pairs.value = Object.entries(obj).map(([key, value]) => ({
        key,
        value: typeof value === 'object' ? JSON.stringify(value) : String(value ?? ''),
      }))
    } catch { return }
  }
  mode.value = m
}

// ── Raw JSON 操作 ──
function handleRawInput() {
  const val = rawText.value.trim()
  if (!val) { rawError.value = ''; emit('update:modelValue', ''); return }
  try {
    JSON.parse(val)
    rawError.value = ''
    emit('update:modelValue', rawText.value)
  } catch (e: any) {
    rawError.value = e.message?.replace('JSON.parse: ', '').substring(0, 60) || 'Invalid'
  }
}

function formatRaw() {
  try {
    const obj = JSON.parse(rawText.value)
    rawText.value = JSON.stringify(obj, null, 2)
    rawError.value = ''
    emit('update:modelValue', rawText.value)
  } catch { /* noop */ }
}

function minifyRaw() {
  try {
    const obj = JSON.parse(rawText.value)
    rawText.value = JSON.stringify(obj)
    rawError.value = ''
    emit('update:modelValue', rawText.value)
  } catch { /* noop */ }
}

function insertTab() {
  const ta = textareaRef.value
  if (!ta) return
  const s = ta.selectionStart, e = ta.selectionEnd
  const v = ta.value
  rawText.value = v.substring(0, s) + '  ' + v.substring(e)
  nextTick(() => { ta.selectionStart = ta.selectionEnd = s + 2 })
}

function syncScroll() {
  if (linesRef.value && textareaRef.value) {
    linesRef.value.scrollTop = textareaRef.value.scrollTop
  }
}
</script>

<style scoped>
.je {
  width: 100%;
  border: 1px solid var(--el-border-color, #dcdfe6);
  border-radius: 4px;
  overflow: hidden;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  font-size: 13px;
}

/* ── Header ── */
.je-header {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 8px;
  height: 32px;
  background: #f8fafc;
  border-bottom: 1px solid var(--el-border-color-lighter, #ebeef5);
}
.je-tabs {
  display: flex;
  gap: 2px;
}
.je-tab {
  padding: 2px 10px;
  border: none;
  background: none;
  color: #6b7280;
  font-size: 12px;
  font-family: inherit;
  cursor: pointer;
  border-radius: 3px;
  transition: all 0.15s;
}
.je-tab:hover { background: #e5e7eb; }
.je-tab.active {
  background: #fff;
  color: #111827;
  box-shadow: 0 1px 2px rgba(0,0,0,0.06);
  font-weight: 500;
}
.je-error { color: #ef4444; font-size: 11px; margin-left: auto; }
.je-valid { color: #16a34a; font-size: 11px; margin-left: auto; }

/* ── Visual Mode ── */
.je-visual {
  padding: 8px;
  min-height: 60px;
}
.je-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  height: 56px;
  color: #9ca3af;
  cursor: pointer;
  border: 1px dashed #d1d5db;
  border-radius: 4px;
  transition: all 0.15s;
  font-size: 13px;
}
.je-empty:hover {
  border-color: var(--el-color-primary, #2563eb);
  color: var(--el-color-primary, #2563eb);
  background: #f0f7ff;
}
.je-empty-icon {
  font-size: 18px;
  font-weight: 300;
  line-height: 1;
}
.je-row {
  display: flex;
  align-items: center;
  gap: 0;
  margin-bottom: 4px;
}
.je-input {
  height: 32px;
  padding: 0 10px;
  border: 1px solid #e5e7eb;
  background: #fff;
  font-size: 13px;
  font-family: 'Geist Mono', 'SF Mono', 'Consolas', monospace;
  color: #111827;
  outline: none;
  transition: border-color 0.15s;
}
.je-input:focus {
  border-color: var(--el-color-primary, #2563eb);
  z-index: 1;
}
.je-input::placeholder { color: #d1d5db; }
.je-key {
  width: 35%;
  flex-shrink: 0;
  border-radius: 4px 0 0 4px;
  border-right: none;
  font-weight: 500;
  color: #6366f1;
}
.je-sep {
  width: 24px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #f3f4f6;
  border-top: 1px solid #e5e7eb;
  border-bottom: 1px solid #e5e7eb;
  color: #9ca3af;
  font-size: 12px;
  flex-shrink: 0;
  user-select: none;
}
.je-value {
  flex: 1;
  min-width: 0;
  border-radius: 0;
  border-right: none;
}
.je-row-btn {
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 1px solid #e5e7eb;
  background: #fff;
  color: #9ca3af;
  cursor: pointer;
  flex-shrink: 0;
  transition: all 0.15s;
}
.je-row-del {
  border-radius: 0 4px 4px 0;
}
.je-row-del:hover {
  background: #fef2f2;
  color: #ef4444;
  border-color: #fca5a5;
}
.je-add {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 4px 10px;
  margin-top: 4px;
  border: none;
  background: none;
  color: var(--el-color-primary, #2563eb);
  font-size: 12px;
  font-family: inherit;
  cursor: pointer;
  border-radius: 3px;
  transition: background 0.15s;
}
.je-add:hover { background: #eff6ff; }

/* ── Raw Mode ── */
.je-raw {
  display: flex;
  flex-direction: column;
}
.je-raw-wrap {
  display: flex;
  min-height: 160px;
  max-height: 320px;
}
.je-lines {
  flex-shrink: 0;
  width: 32px;
  padding: 8px 0;
  background: #f8fafc;
  border-right: 1px solid #ebeef5;
  overflow: hidden;
  user-select: none;
}
.je-ln {
  height: 20px;
  line-height: 20px;
  text-align: right;
  padding-right: 6px;
  color: #c4c7cc;
  font-size: 11px;
  font-family: 'Geist Mono', 'SF Mono', monospace;
}
.je-code {
  flex: 1;
  padding: 8px 10px;
  border: none;
  outline: none;
  resize: none;
  background: #fff;
  color: #1f2937;
  font-family: 'Geist Mono', 'SF Mono', 'Consolas', monospace;
  font-size: 13px;
  line-height: 20px;
  tab-size: 2;
  min-height: 160px;
  max-height: 320px;
  overflow-y: auto;
}
.je-code::placeholder { color: #d1d5db; }
.je-raw-actions {
  display: flex;
  gap: 4px;
  padding: 4px 8px;
  border-top: 1px solid #ebeef5;
  background: #f8fafc;
}
.je-btn {
  padding: 2px 8px;
  border: 1px solid #e5e7eb;
  border-radius: 3px;
  background: #fff;
  color: #374151;
  font-size: 11px;
  font-family: inherit;
  cursor: pointer;
  transition: all 0.15s;
}
.je-btn:hover {
  background: #eff6ff;
  border-color: var(--el-color-primary, #2563eb);
  color: var(--el-color-primary, #2563eb);
}
</style>
