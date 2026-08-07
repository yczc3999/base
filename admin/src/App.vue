<template>
  <!-- el-config-provider: Element Plus 组件语言随 i18n 切换 -->
  <el-config-provider :locale="elLocale">
    <a href="#main-content" class="skip-link">跳到主要内容</a>
    <router-view />
    <!-- 全局确认弹窗（confirmDialog 命令式触发） -->
    <AppConfirm />
  </el-config-provider>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import en from 'element-plus/es/locale/lang/en'
import { useThemeStore } from '@/stores/theme'
import AppConfirm from '@/components/AppConfirm/index.vue'

const { locale } = useI18n()
const elLocale = computed(() => (locale.value === 'en-US' ? en : zhCn))

// P2-4: 应用持久化主题（light/dark）
useThemeStore().init()
</script>
