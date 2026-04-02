<template>
  <div v-loading="loading" style="max-width:700px">
    <h2 style="font-size:18px;font-weight:600;margin-bottom:20px">站点设置</h2>
    <div style="background:var(--bg-card);border:1px solid var(--border);border-radius:var(--radius);padding:24px">
      <el-form label-width="100px">
        <el-form-item label="站点名称"><el-input v-model="form.name" placeholder="Base Platform" /></el-form-item>
        <el-form-item label="站点描述"><el-input v-model="form.description" type="textarea" :rows="2" /></el-form-item>
        <el-form-item label="Logo URL"><el-input v-model="form.logo" placeholder="https://..." /></el-form-item>
        <el-form-item label="Favicon"><el-input v-model="form.favicon" placeholder="https://..." /></el-form-item>
        <el-form-item label="ICP 备案"><el-input v-model="form.icp" placeholder="京ICP备xxxxxxxx号" /></el-form-item>
        <el-form-item label="版权信息"><el-input v-model="form.copyright" placeholder="© 2026 Base Platform" /></el-form-item>
        <el-form-item>
          <el-button type="primary" @click="save">保存</el-button>
        </el-form-item>
      </el-form>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { createSettingApi } from '@/api/settings'
import { ElMessage } from 'element-plus'

const api = createSettingApi('site')
const loading = ref(false)
const form = reactive({ name: '', description: '', logo: '', favicon: '', icp: '', copyright: '' })

onMounted(async () => {
  loading.value = true
  const data = await api.getAll()
  Object.assign(form, data)
  loading.value = false
})

async function save() {
  await api.setMany(form)
  ElMessage.success('保存成功')
}
</script>
