import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import AutoImport from 'unplugin-auto-import/vite'
import Components from 'unplugin-vue-components/vite'
import { ElementPlusResolver } from 'unplugin-vue-components/resolvers'
import path from 'path'

export default defineConfig({
  plugins: [
    vue(),
    AutoImport({
      resolvers: [ElementPlusResolver()],
      imports: ['vue', 'vue-router', 'pinia'],
      dts: 'src/auto-imports.d.ts',
    }),
    Components({
      resolvers: [ElementPlusResolver()],
      dts: 'src/components.d.ts',
    }),
  ],
  resolve: {
    alias: [
      { find: '@', replacement: path.resolve(__dirname, 'src') },
      // editor-for-vue 的 ESM 构建 `import t from "vue"`（vue 无 default 导出），
      // rolldown 严格模式下报错；指向 CJS 构建走 interop
      {
        find: '@wangeditor/editor-for-vue',
        replacement: path.resolve(
          __dirname,
          'node_modules/@wangeditor/editor-for-vue/dist/index.js',
        ),
      },
      // @wangeditor/editor-for-vue 期望 vue 有 default 导出，runtime-only 版没给
      { find: 'vue', replacement: 'vue/dist/vue.esm-bundler.js' },
    ],
  },
  optimizeDeps: {
    exclude: ['@wangeditor/editor-for-vue'],
  },
  server: {
    host: '0.0.0.0',
    port: Number(process.env.ADMIN_PORT) || 9200,
    strictPort: false,
    proxy: {
      '/api': {
        target: `http://127.0.0.1:${process.env.API_PORT || 9000}`,
        changeOrigin: true,
      },
      '/uploads': {
        target: `http://127.0.0.1:${process.env.API_PORT || 9000}`,
        changeOrigin: true,
      },
    },
  },
})
