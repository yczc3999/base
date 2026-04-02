<template>
  <div class="tags-view">
    <div
      v-for="tag in tagsStore.visitedViews"
      :key="tag.path"
      class="tag-item"
      :class="{ active: route.path === tag.path }"
      @click="router.push(tag.path)"
    >
      <span class="tag-icon">{{ getTagIcon(tag) }}</span>
      <span>{{ tag.title }}</span>
      <span v-if="!tag.affix" class="tag-close" @click.stop="closeTag(tag.path)">×</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { useTagsStore } from '@/stores/tags'
import { useRoute, useRouter } from 'vue-router'

const tagsStore = useTagsStore()
const route = useRoute()
const router = useRouter()

watch(route, (r) => { tagsStore.addView(r) }, { immediate: true })

function closeTag(path: string) {
  tagsStore.removeView(path)
  if (route.path === path) {
    const last = tagsStore.visitedViews[tagsStore.visitedViews.length - 1]
    router.push(last?.path || '/dashboard')
  }
}

const iconMap: Record<string, string> = {
  '/dashboard': '▦', '/profile': '◎', '/message': '🔔',
  '/system/user': '👤', '/system/role': '🛡', '/system/menu': '☰',
  '/system/setting': '⊞', '/system/log/operation': '◉', '/system/log/login': '→',
  '/settings/site': '🌐', '/settings/sms': '💬', '/settings/storage': '💾',
  '/settings/notify': '🔔', '/settings/payment': '💳',
}

function getTagIcon(tag: any): string {
  if (tag.icon) return tag.icon
  return iconMap[tag.path] || '·'
}
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
  transition: all var(--transition-fast);
  user-select: none;

  &:hover { color: var(--primary); border-color: var(--primary); }

  &.active {
    background: var(--primary);
    color: white;
    border-color: var(--primary);
  }

  .tag-icon { font-size: 11px; }

  .tag-close {
    font-size: 14px;
    line-height: 1;
    width: 14px; height: 14px;
    display: flex; align-items: center; justify-content: center;
    margin-left: 2px;

    &:hover { background: rgba(0,0,0,0.15); border-radius: 1px; }
  }
}
</style>
