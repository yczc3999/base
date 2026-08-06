/** i18n 实例 — 语言持久化到 localStorage, 业务页未翻译字符串回退 zh-CN */
import { createI18n } from 'vue-i18n'
import zhCN from './zh-CN'
import enUS from './en-US'

export const LOCALE_KEY = 'base_locale'
export type Locale = 'zh-CN' | 'en-US'

export function getSavedLocale(): Locale {
  const saved = localStorage.getItem(LOCALE_KEY) as Locale | null
  return saved === 'en-US' ? 'en-US' : 'zh-CN'
}

export function saveLocale(locale: Locale) {
  localStorage.setItem(LOCALE_KEY, locale)
}

const i18n = createI18n({
  legacy: false,          // composition API 模式
  globalInjection: true,  // 模板可直接用 $t
  locale: getSavedLocale(),
  fallbackLocale: 'zh-CN',
  messages: { 'zh-CN': zhCN, 'en-US': enUS },
})

export default i18n
