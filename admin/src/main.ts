import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import 'element-plus/theme-chalk/dark/css-vars.css'  // P2-4 暗色变量(html.dark 门控)
import App from './App.vue'
import router from './router'
import i18n from './locales'
import './styles/index.scss'

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.use(i18n)
app.use(ElementPlus, { size: 'default' })
app.mount('#app')
