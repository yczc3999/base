// ═══════════════════════════════════════════════
// ESLint 9 — flat config
// vue + typescript recommended, zero-formatting (prettier)
// ═══════════════════════════════════════════════
import js from '@eslint/js'
import pluginVue from 'eslint-plugin-vue'
import tseslint from 'typescript-eslint'
import prettier from 'eslint-config-prettier'
import globals from 'globals'

/**
 * unplugin-auto-import 注入的全局 API（vue / vue-router / pinia）。
 * 与 src/auto-imports.d.ts 保持一致；该文件由 vite 构建时重新生成，
 * 若新增 auto-import 请同步本列表。
 */
const autoImportGlobals = {
  acceptHMRUpdate: 'readonly',
  computed: 'readonly',
  createApp: 'readonly',
  createPinia: 'readonly',
  customRef: 'readonly',
  defineAsyncComponent: 'readonly',
  defineComponent: 'readonly',
  defineStore: 'readonly',
  effectScope: 'readonly',
  EffectScope: 'readonly',
  getActivePinia: 'readonly',
  getCurrentInstance: 'readonly',
  getCurrentScope: 'readonly',
  getCurrentWatcher: 'readonly',
  h: 'readonly',
  inject: 'readonly',
  isProxy: 'readonly',
  isReactive: 'readonly',
  isReadonly: 'readonly',
  isRef: 'readonly',
  isShallow: 'readonly',
  mapActions: 'readonly',
  mapGetters: 'readonly',
  mapState: 'readonly',
  mapStores: 'readonly',
  mapWritableState: 'readonly',
  markRaw: 'readonly',
  nextTick: 'readonly',
  onActivated: 'readonly',
  onBeforeMount: 'readonly',
  onBeforeRouteLeave: 'readonly',
  onBeforeRouteUpdate: 'readonly',
  onBeforeUnmount: 'readonly',
  onBeforeUpdate: 'readonly',
  onDeactivated: 'readonly',
  onErrorCaptured: 'readonly',
  onMounted: 'readonly',
  onRenderTracked: 'readonly',
  onRenderTriggered: 'readonly',
  onScopeDispose: 'readonly',
  onServerPrefetch: 'readonly',
  onUnmounted: 'readonly',
  onUpdated: 'readonly',
  onWatcherCleanup: 'readonly',
  provide: 'readonly',
  reactive: 'readonly',
  readonly: 'readonly',
  ref: 'readonly',
  resolveComponent: 'readonly',
  setActivePinia: 'readonly',
  setMapStoreSuffix: 'readonly',
  shallowReactive: 'readonly',
  shallowReadonly: 'readonly',
  shallowRef: 'readonly',
  storeToRefs: 'readonly',
  toRaw: 'readonly',
  toRef: 'readonly',
  toRefs: 'readonly',
  toValue: 'readonly',
  triggerRef: 'readonly',
  unref: 'readonly',
  useAttrs: 'readonly',
  useCssModule: 'readonly',
  useCssVars: 'readonly',
  useId: 'readonly',
  useLink: 'readonly',
  useModel: 'readonly',
  useRoute: 'readonly',
  useRouter: 'readonly',
  useSlots: 'readonly',
  useTemplateRef: 'readonly',
  watch: 'readonly',
  watchEffect: 'readonly',
  watchPostEffect: 'readonly',
  watchSyncEffect: 'readonly',
}

export default tseslint.config(
  {
    // 生成物 / 依赖 / 自动生成 d.ts 不 lint
    ignores: [
      'dist',
      'node_modules',
      'public',
      'auto-imports.d.ts',
      'components.d.ts',
      'vite.config.ts',
    ],
  },

  // 纯 JS（eslint.config.js 自身等）→ core recommended
  js.configs.recommended,

  // TS 推荐规则
  ...tseslint.configs.recommended,

  // Vue 推荐规则（含 vue/no-unused-vars，懂 template 用法）
  ...pluginVue.configs['flat/recommended'],

  // 全局：浏览器环境 + auto-import 注入的 Vue/pinia/router API
  {
    files: ['**/*.{ts,tsx,vue}'],
    languageOptions: {
      globals: {
        ...globals.browser,
        ...autoImportGlobals,
      },
    },
  },

  // Vue SFC：script 使用 TS parser
  {
    files: ['**/*.vue'],
    languageOptions: {
      parserOptions: {
        parser: tseslint.parser,
        ecmaVersion: 'latest',
        sourceType: 'module',
        extraFileExtensions: ['.vue'],
      },
    },
    rules: {
      // 本项目允许单字视图名（one-word views）
      'vue/multi-word-component-names': 'off',
      // Vue SFC 的 <script setup lang="ts"> 同样属于业务代码（动态后端结构），
      // 与下方 .ts 文件的放宽保持一致，不再对 .vue 单独报 no-explicit-any。
      '@typescript-eslint/no-explicit-any': 'off',
      // 属性顺序（定义 → 渲染 → 条件 → 全局 → 双向 → 事件 → 内容）
      'vue/attributes-order': [
        'error',
        {
          order: [
            'DEFINITION',
            'LIST_RENDERING',
            'CONDITIONALS',
            'RENDER_MODIFIERS',
            'GLOBAL',
            'UNIQUE',
            'TWO_WAY_BINDING',
            'OTHER_DIRECTIVES',
            'OTHER_ATTR',
            'EVENTS',
            'CONTENT',
          ],
          alphabetical: false,
        },
      ],
    },
  },

  // TS 文件：未使用变量 = error（前缀 _ 豁免）
  {
    files: ['**/*.ts', '**/*.tsx'],
    rules: {
      'no-unused-vars': 'off',
      '@typescript-eslint/no-unused-vars': [
        'error',
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_' },
      ],
      // 生产代码放宽：业务里大量用 any（后端动态结构）
      '@typescript-eslint/no-explicit-any': 'off',
    },
  },

  // 关闭与 Prettier 冲突的格式类规则
  prettier,
)
