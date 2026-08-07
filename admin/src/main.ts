import { createApp } from 'vue'
import { createPinia } from 'pinia'
import { VueQueryPlugin } from '@tanstack/vue-query'
// 按需引入：unplugin-vue-components ElementPlusResolver 自动处理模板内组件/directive 的 JS+CSS
import 'element-plus/theme-chalk/dark/css-vars.css'  // P2-4 暗色变量(html.dark 门控)
// 函数式调用（ElMessage）的样式需显式全局引入（JS 已在各文件按需 import）
import 'element-plus/es/components/message/style/css'
import App from './App.vue'
import router from './router'
import i18n from './locales'
import { queryClient } from '@/utils/queryClient'
import './styles/index.scss'

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.use(i18n)
// F1 · TanStack Query：统一数据请求缓存/轮询/状态管理
app.use(VueQueryPlugin, { queryClient })
app.mount('#app')
