<template>
  <teleport to="body">
    <transition name="confirm-fade">
      <div
        v-if="confirmState.visible"
        class="confirm-overlay"
        :class="{ 'no-overlay-close': !confirmState.closeOnClickOverlay }"
        tabindex="-1"
        @click.self="onOverlayClick"
        @keydown.esc="onEsc"
      >
        <div
          class="confirm-card"
          role="dialog"
          aria-modal="true"
          :aria-label="confirmState.title"
          :style="{ width: confirmState.width }"
        >
          <!-- 头部：大色块图标 + 标题 -->
          <div class="confirm-head">
            <div class="confirm-icon" :class="meta.iconClass">
              <component :is="meta.icon" :size="22" :stroke-width="2.2" />
            </div>
            <h3 class="confirm-title">{{ confirmState.title }}</h3>
          </div>

          <!-- 正文：pre-wrap 支持 message 内 \n 换行 -->
          <div class="confirm-body">{{ confirmState.message }}</div>

          <!-- 操作区 -->
          <div class="confirm-actions">
            <el-button
              v-if="confirmState.showCancel"
              class="confirm-cancel"
              :icon="X"
              @click="onCancel"
            >
              {{ confirmState.cancelButtonText }}
            </el-button>
            <el-button
              ref="confirmBtnRef"
              class="confirm-ok"
              :type="meta.buttonType"
              :icon="Check"
              @click="onConfirm"
            >
              {{ confirmState.confirmButtonText }}
            </el-button>
          </div>
        </div>
      </div>
    </transition>
  </teleport>
</template>

<script setup lang="ts">
import { nextTick, watch } from 'vue'
import {
  TriangleAlert,
  CircleAlert,
  Info,
  CircleCheck,
  X,
  Check,
  type LucideIcon,
} from 'lucide-vue-next'
import {
  confirmState,
  resolveConfirm,
  cancelConfirm,
  type ConfirmType,
} from '@/utils/confirm'

const confirmBtnRef = ref<{ $el: HTMLElement }>()

/** 集中 type 映射：图标 / 色块 / 确认按钮色 三处保持一致，只改这一处 */
const TYPE_META: Record<ConfirmType, {
  icon: LucideIcon
  iconClass: string
  buttonType: 'primary' | 'danger' | 'success' | 'warning'
}> = {
  warning: { icon: TriangleAlert, iconClass: 'is-warning', buttonType: 'primary' },
  error:   { icon: CircleAlert,   iconClass: 'is-error',   buttonType: 'danger' },
  info:    { icon: Info,          iconClass: 'is-info',    buttonType: 'primary' },
  success: { icon: CircleCheck,   iconClass: 'is-success', buttonType: 'success' },
}

const meta = computed(() => TYPE_META[confirmState.type] || TYPE_META.warning)

function onConfirm() {
  resolveConfirm()
}

function onCancel() {
  cancelConfirm()
}

function onOverlayClick() {
  if (confirmState.closeOnClickOverlay) cancelConfirm()
}

function onEsc() {
  if (confirmState.closeOnEsc) cancelConfirm()
}

// 打开时聚焦确认按钮（回车即确认），关闭时归还焦点给 body 避免焦点残留
watch(
  () => confirmState.visible,
  async (visible) => {
    if (visible) {
      await nextTick()
      confirmBtnRef.value?.$el?.focus()
    } else {
      if (document.activeElement instanceof HTMLElement) document.activeElement.blur()
    }
  },
)
</script>

<style scoped lang="scss">
.confirm-overlay {
  position: fixed;
  inset: 0;
  z-index: 3000;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 16px;
  background: rgba(15, 23, 42, 0.45); // slate-900 半透明遮罩，纯色无模糊
}

.confirm-card {
  max-width: 90vw;
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 4px; // 弹窗浮层允许到 4px（规范上限）
  padding: 24px;
}

.confirm-head {
  display: flex;
  align-items: center;
  gap: 12px;
}

.confirm-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  flex-shrink: 0;
  border-radius: 4px;

  &.is-warning { background: var(--warning-bg); color: var(--warning); }
  &.is-error   { background: var(--danger-bg);   color: var(--danger); }
  &.is-info    { background: var(--primary-bg);  color: var(--primary); }
  &.is-success { background: var(--success-bg);  color: var(--success); }
}

.confirm-title {
  font-size: var(--text-lg);
  font-weight: 600;
  color: var(--text-primary);
  margin: 0;
  line-height: 1.4;
}

.confirm-body {
  margin-top: 16px;
  font-size: var(--text-base);
  color: var(--text-secondary);
  line-height: 1.6;
  white-space: pre-wrap; // 支持 message 内换行
  word-break: break-word;
}

.confirm-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 24px;
  padding-top: 16px;
  border-top: 1px solid var(--border-light);
}

.confirm-cancel,
.confirm-ok {
  min-width: 88px;
}
</style>
