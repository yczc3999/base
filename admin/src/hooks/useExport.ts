/**
 * useExport — 数据导出 Hook
 *
 * 封装：触发导出 → 轮询进度 → 自动下载 的完整流程。
 *
 * 用法：
 *   const { exporting, progress, handleExport } = useExport(api)
 *   // handleExport(filters) 触发导出
 */

import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import type { CrudApi } from '@/api/crud'
import { getExportProgress, downloadExportFile } from '@/api/modules/export'

const POLL_INTERVAL = 1500 // 轮询间隔（ms）

export function useExport(api: CrudApi) {
  const exporting = ref(false)
  const progress = ref(0)

  let pollTimer: ReturnType<typeof setInterval> | null = null

  function stopPoll() {
    if (pollTimer) {
      clearInterval(pollTimer)
      pollTimer = null
    }
  }

  async function handleExport(filters?: Record<string, any>) {
    if (exporting.value) return

    exporting.value = true
    progress.value = 0

    try {
      // 1. 触发导出
      const result = await api.doExport({ filters })
      const key = result?.key
      if (!key) {
        ElMessage.error('导出失败：未获取到任务标识')
        exporting.value = false
        return
      }

      // 2. 轮询进度
      pollTimer = setInterval(async () => {
        try {
          const data = await getExportProgress(key)

          if (data.status === 2) {
            // 失败
            stopPoll()
            exporting.value = false
            progress.value = 0
            ElMessage.error('导出失败，请重试')
            return
          }

          progress.value = data.progress

          if (data.status === 1) {
            // 完成 → 下载
            stopPoll()
            progress.value = 100

            try {
              await downloadExportFile(key)
              ElMessage.success('导出完成')
            } catch {
              ElMessage.error('下载失败')
            }

            exporting.value = false
            progress.value = 0
          }
        } catch {
          stopPoll()
          exporting.value = false
          progress.value = 0
          ElMessage.error('查询导出进度失败')
        }
      }, POLL_INTERVAL)

    } catch {
      exporting.value = false
      progress.value = 0
    }
  }

  return {
    exporting,
    progress,
    handleExport,
  }
}
