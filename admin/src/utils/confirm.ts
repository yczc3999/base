import { reactive } from 'vue'

export type ConfirmType = 'warning' | 'error' | 'info' | 'success'

export interface ConfirmOptions {
  /** 语义类型 → 图标色块 / 确认按钮色 由此驱动 */
  type?: ConfirmType
  /** 确认按钮文案，默认「确定」 */
  confirmButtonText?: string
  /** 取消按钮文案，默认「取消」 */
  cancelButtonText?: string
  /** 隐藏取消按钮（纯告知型），默认 false */
  showCancel?: boolean
  /** 点遮罩关闭，默认 true */
  closeOnClickOverlay?: boolean
  /** Esc 关闭，默认 true */
  closeOnEsc?: boolean
  /** 弹窗宽度，默认 400 */
  width?: number | string
}

interface ConfirmState extends Required<Omit<ConfirmOptions, 'type' | 'width'>> {
  visible: boolean
  title: string
  message: string
  type: ConfirmType
  width: string
  resolveFn?: (value: boolean) => void
  rejectFn?: (reason: string) => void
}

/** 全局单例确认弹窗状态 —— AppConfirm 组件读取渲染，confirmDialog 写入触发 */
export const confirmState = reactive<ConfirmState>({
  visible: false,
  title: '',
  message: '',
  type: 'warning',
  confirmButtonText: '确定',
  cancelButtonText: '取消',
  showCancel: true,
  closeOnClickOverlay: true,
  closeOnEsc: true,
  width: '400px',
})

/**
 * 命令式全局确认弹窗。签名对齐 ElMessageBox.confirm：
 *   await confirmDialog(message, title, options)
 * 确认 → resolve(true)；取消 / 点遮罩 / Esc → reject('cancel')。
 * 调用方无需改 try/catch 结构，仅替换函数名即可。
 */
export function confirmDialog(
  message: string,
  title = '提示',
  options: ConfirmOptions = {},
): Promise<boolean> {
  confirmState.message = message
  confirmState.title = title
  confirmState.type = options.type || 'warning'
  confirmState.confirmButtonText = options.confirmButtonText || '确定'
  confirmState.cancelButtonText = options.cancelButtonText || '取消'
  confirmState.showCancel = options.showCancel !== false
  confirmState.closeOnClickOverlay = options.closeOnClickOverlay !== false
  confirmState.closeOnEsc = options.closeOnEsc !== false
  confirmState.width =
    typeof options.width === 'number' ? `${options.width}px` : options.width || '400px'
  confirmState.visible = true
  return new Promise<boolean>((resolve, reject) => {
    confirmState.resolveFn = resolve
    confirmState.rejectFn = reject
  })
}

/** 组件内部：关闭并 resolve(true) */
export function resolveConfirm(): void {
  confirmState.visible = false
  confirmState.resolveFn?.(true)
  clearHandlers()
}

/** 组件内部：关闭并 reject('cancel') */
export function cancelConfirm(): void {
  confirmState.visible = false
  confirmState.rejectFn?.('cancel')
  clearHandlers()
}

function clearHandlers(): void {
  confirmState.resolveFn = undefined
  confirmState.rejectFn = undefined
}
