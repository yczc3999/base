<template>
  <div class="tags-view" role="navigation" aria-label="页签">
    <div
      v-for="tag in tagsStore.visitedViews"
      :key="tag.path"
      class="tag-item"
      :class="{ active: route.path === tag.path }"
      role="button"
      tabindex="0"
      @click="router.push(tag.path)"
      @keydown.enter.prevent="router.push(tag.path)"
      @keydown.space.prevent="router.push(tag.path)"
      @contextmenu.prevent="openContext(tag, $event)"
    >
      <el-icon class="tag-icon"><component :is="getMenuIcon(tag.icon)" :size="14" /></el-icon>
      <span class="tag-title">{{ tag.title }}</span>
      <span
        v-if="!tag.affix"
        class="tag-close"
        role="button"
        tabindex="0"
        aria-label="关闭"
        @click.stop="closeTag(tag.path)"
        @keydown.enter.prevent.stop="closeTag(tag.path)"
        @keydown.space.prevent.stop="closeTag(tag.path)"
      >×</span>
    </div>

    <!-- 右键菜单 -->
    <teleport to="body">
      <div
        v-if="ctxMenu.visible"
        class="tags-ctx"
        :style="{ left: ctxMenu.x + 'px', top: ctxMenu.y + 'px' }"
        @click.stop
      >
        <button class="ctx-item" :disabled="noOtherTags" @click="closeOthers">
          <component :is="X" :size="14" />
          <span>关闭其他</span>
        </button>
        <button class="ctx-item" :disabled="noOtherTags" @click="closeLeft">
          <component :is="ChevronLeft" :size="14" />
          <span>关闭左侧</span>
        </button>
        <button class="ctx-item" @click="closeRight">
          <component :is="ChevronRight" :size="14" />
          <span>关闭右侧</span>
        </button>
        <div class="ctx-divider" />
        <button class="ctx-item" @click="closeAll">
          <component :is="XCircle" :size="14" />
          <span>全部关闭</span>
        </button>
      </div>
    </teleport>
  </div>
</template>

<script setup lang="ts">
import { reactive, computed } from 'vue'
import { useTagsStore } from '@/stores/tags'
import { useRoute, useRouter } from 'vue-router'
import { getMenuIcon } from '@/utils/menuIcons'
import { X, ChevronLeft, ChevronRight, XCircle } from 'lucide-vue-next'

const tagsStore = useTagsStore()
const route = useRoute()
const router = useRouter()

watch(route, (r) => { tagsStore.addView(r) }, { immediate: true })

// 关闭并跳转：若关的是当前页，跳到最近一个可见 tab
function closeTag(path: string) {
  tagsStore.removeView(path)
  if (route.path === path) {
    const last = tagsStore.visitedViews[tagsStore.visitedViews.length - 1]
    router.push(last?.path || '/dashboard')
  }
}

// ── 右键菜单 ──
const ctxMenu = reactive({ visible: false, x: 0, y: 0 })

const noOtherTags = computed(() => tagsStore.visitedViews.length <= 1)

function openContext(tag: TagView, e: MouseEvent) {
  ctxMenu.x = e.clientX
  ctxMenu.y = e.clientY
  ctxMenu.visible = true
  ctxTag = tag
}

// 关闭菜单：点击外部 / 滚动 / 路由变化
function hideCtx() { ctxMenu.visible = false }
onMounted(() => {
  document.addEventListener('click', hideCtx)
  document.addEventListener('contextmenu', (e) => { if (!(e.target as HTMLElement)?.closest('.tags-ctx')) hideCtx() })
  window.addEventListener('scroll', hideCtx, true)
})
onBeforeUnmount(() => {
  document.removeEventListener('click', hideCtx)
  window.removeEventListener('scroll', hideCtx, true)
})

function closeOthers() {
  tagsStore.removeOthers(ctxTag.path)
  if (!tagsStore.visitedViews.some((v) => v.path === route.path)) {
    router.push(ctxTag.path)
  }
  hideCtx()
}

function closeLeft() {
  const idx = tagsStore.visitedViews.findIndex((v) => v.path === ctxTag.path)
  if (idx > 0) {
    const keep = tagsStore.visitedViews.slice(idx)
    tagsStore.visitedViews = keep
    tagsStore.cachedViews = keep.filter((v) => v.name).map((v) => toPascal(v.name))
    if (!keep.some((v) => v.path === route.path)) router.push(ctxTag.path)
  }
  hideCtx()
}

function closeRight() {
  const idx = tagsStore.visitedViews.findIndex((v) => v.path === ctxTag.path)
  if (idx >= 0 && idx < tagsStore.visitedViews.length - 1) {
    const keep = tagsStore.visitedViews.slice(0, idx + 1)
    tagsStore.visitedViews = keep
    tagsStore.cachedViews = keep.filter((v) => v.name).map((v) => toPascal(v.name))
    if (!keep.some((v) => v.path === route.path)) router.push(ctxTag.path)
  }
  hideCtx()
}

function closeAll() {
  tagsStore.removeAll()
  router.push('/dashboard')
  hideCtx()
}

// tags store 内 toPascal 未导出，这里复刻一份
function toPascal(slug: string): string {
  return slug.split(/[-_]/).map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join('')
}

// ctxTag 持有（类型）
interface TagView { path: string; name: string; title: string; affix?: boolean; icon?: string }
let ctxTag: TagView = { path: '', name: '', title: '' }
</script>

<style scoped lang="scss">
.tags-view {
  height: var(--tags-height);
  background: var(--bg-card);
  border-bottom: 1px solid var(--border);
  display: flex;
  align-items: center;
  padding: 0 var(--space-md);
  gap: 4px;
  overflow-x: auto;
  flex-shrink: 0;

  &::-webkit-scrollbar { height: 0; }
}

.tag-item {
  display: flex;
  align-items: center;
  gap: 4px;
  height: 26px;
  padding: 0 10px;
  font-size: var(--text-xs);
  color: var(--text-secondary);
  background: var(--bg-page);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  cursor: pointer;
  white-space: nowrap;
  transition: background var(--transition-fast), color var(--transition-fast), border-color var(--transition-fast);
  user-select: none;

  &:hover { color: var(--primary); border-color: var(--primary); }

  &.active {
    background: var(--primary);
    color: white;
    border-color: var(--primary);
  }

  .tag-icon {
    font-size: 13px;
    display: inline-flex;
    align-items: center;
  }

  .tag-close {
    font-size: 14px;
    line-height: 1;
    width: 14px; height: 14px;
    display: flex; align-items: center; justify-content: center;
    margin-left: 2px;
    color: inherit;
    opacity: 0.75;

    &:hover { opacity: 1; background: rgba(0,0,0,0.15); border-radius: 1px; }
  }
}

/* ── 右键菜单（纯平面，圆角 4px）── */
.tags-ctx {
  position: fixed;
  z-index: 4000;
  min-width: 140px;
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 4px;
  animation: ctx-in 150ms var(--transition-base) both;
}
@keyframes ctx-in {
  from { opacity: 0; }
  to { opacity: 1; }
}

.ctx-item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 7px 10px;
  font-size: var(--text-xs);
  color: var(--text-primary);
  background: transparent;
  border: none;
  border-radius: var(--radius-sm);
  cursor: pointer;
  text-align: left;

  &:hover:not(:disabled) { background: var(--primary-bg); color: var(--primary); }
  &:disabled { color: var(--text-disabled); cursor: not-allowed; }
}

.ctx-divider {
  height: 1px;
  background: var(--border-light);
  margin: 4px 0;
}
</style>
