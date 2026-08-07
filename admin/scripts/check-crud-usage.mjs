#!/usr/bin/env node
/**
 * F4 — CrudTable 使用门禁（构建期扫描，仅告警不阻断）。
 *
 * 扫描 src/views 目录下所有 index.vue，找出没有使用 CrudTable 的页面。
 *  - 全量列表：预期中「合法自定义页」（dashboard / monitor / login / settings / seo / error 等）打印为提示；
 *  - CRUD 域（views/content 与 views/system）：未用 CrudTable 的页面打印为显眼警告。
 *
 * 退出码恒为 0（warning only），方便接入 CI 而不打断流水线。
 */
import { readdirSync, readFileSync, statSync, existsSync } from 'node:fs'
import { join, relative, sep } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = fileURLToPath(new URL('..', import.meta.url))
const viewsRoot = join(root, 'src', 'views')

/** 递归收集所有 index.vue */
function collectIndexVue(dir) {
  const out = []
  for (const name of readdirSync(dir)) {
    const full = join(dir, name)
    const st = statSync(full)
    if (st.isDirectory()) {
      out.push(...collectIndexVue(full))
    } else if (name === 'index.vue') {
      out.push(full)
    }
  }
  return out
}

function usesCrudTable(file) {
  const src = readFileSync(file, 'utf8')
  // 导入了 CrudTable / SchemaCrudPage，或模板中出现 <CrudTable / <SchemaCrudPage
  // （SchemaCrudPage 内部渲染 CrudTable，视为已使用 CrudTable）
  return /import\s+CrudTable/.test(src) || /<CrudTable[>\s]/.test(src)
    || /import\s+SchemaCrudPage/.test(src) || /<SchemaCrudPage[>\s]/.test(src)
}

const files = existsSync(viewsRoot) ? collectIndexVue(viewsRoot) : []
const noCrud = files.filter((f) => !usesCrudTable(f)).map((f) => relative(root, f).split(sep).join('/'))

/** CRUD 域：content / system 下应当走 CrudTable 的目录 */
const crudDomains = ['content', 'system']
const isCrudDomain = (rel) => crudDomains.some((d) => rel.startsWith(`src/views/${d}/`))

const warned = noCrud.filter(isCrudDomain)
const info = noCrud.filter((rel) => !isCrudDomain(rel))

console.log('')
console.log('━━━ F4 · CrudTable 使用门禁 ━━━')
console.log(`扫描页面总数：${files.length}  |  使用 CrudTable：${files.length - noCrud.length}  |  未使用：${noCrud.length}`)

if (warned.length) {
  console.log('')
  console.log('⚠️  [WARN] CRUD 域（content/ / system/）未使用 CrudTable 的页面：')
  for (const rel of warned) console.log(`   ✗ ${rel}`)
  console.log('   这些属于 CRUD 域，通常应基于 CrudTable 声明式搭建。')
} else {
  console.log('')
  console.log('✅  CRUD 域（content/ / system/）全部页面均已使用 CrudTable。')
}

if (info.length) {
  console.log('')
  console.log('ℹ️  [INFO] 非 CRUD 域未使用 CrudTable（合法自定义页，跳过）：')
  for (const rel of info) console.log(`   · ${rel}`)
}

console.log('')
console.log('（门禁为 warning only，退出码 0）')
