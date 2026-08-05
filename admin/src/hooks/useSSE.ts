/**
 * useSSE — 统一的 SSE 流式读取 Hook
 *
 * 封装 fetch POST + ReadableStream 逐行解析的通用逻辑，
 * 供 article/keyword 等页面复用。避免每页重复实现。
 *
 * 用法：
 *   const { run } = useSSE({
 *     url: '/api/admin/article/gen-from-tags-stream',
 *     onEvent: (evt) => { /* 处理每个 data: 事件 *\/ },
 *     onDone: () => { /* 流结束 *\/ },
 *     onError: (msg) => { /* 请求失败 *\/ },
 *   })
 *   await run(body)
 */

import { ElMessage } from 'element-plus'

export interface SSEOptions {
  /** POST 目标 URL */
  url: string
  /** 每个 data: 事件回调 */
  onEvent: (evt: any) => void
  /** 流结束回调（可选） */
  onDone?: () => void
  /** 请求失败回调（可选，默认弹 ElMessage.error） */
  onError?: (msg: string) => void
}

export function useSSE(options: SSEOptions) {
  const { url, onEvent, onDone, onError } = options

  async function run(body: any = {}): Promise<void> {
    const token = localStorage.getItem('access_token') || ''
    let resp: Response
    try {
      resp = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
        body: JSON.stringify(body),
      })
    } catch {
      const msg = '请求失败'
      if (onError) onError(msg)
      else ElMessage.error(msg)
      return
    }

    if (!resp.ok || !resp.body) {
      const msg = '请求失败'
      if (onError) onError(msg)
      else ElMessage.error(msg)
      return
    }

    const reader = resp.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''

    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue
        try {
          const evt = JSON.parse(line.slice(6))
          onEvent(evt)
          if (evt.type === 'done') {
            if (onDone) onDone()
          }
        } catch {
          // 忽略无法解析的行
        }
      }
    }
  }

  return { run }
}
