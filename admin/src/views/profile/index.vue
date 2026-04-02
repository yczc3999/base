<template>
  <div style="max-width:600px">
    <h2 style="font-size:18px;font-weight:600;margin-bottom:20px">个人中心</h2>
    <div style="background:var(--bg-card);border:1px solid var(--border);border-radius:var(--radius);padding:24px">
      <el-form :model="form" label-width="80px">
        <el-form-item label="用户名"><el-input :model-value="userStore.userInfo?.username" disabled /></el-form-item>
        <el-form-item label="昵称"><el-input v-model="form.nickname" /></el-form-item>
        <el-form-item label="邮箱"><el-input v-model="form.email" /></el-form-item>
        <el-form-item label="手机号"><el-input v-model="form.phone" /></el-form-item>
        <el-form-item><el-button type="primary" @click="saveProfile">保存</el-button></el-form-item>
      </el-form>
    </div>
    <div style="background:var(--bg-card);border:1px solid var(--border);border-radius:var(--radius);padding:24px;margin-top:16px">
      <h3 style="font-size:16px;font-weight:600;margin-bottom:16px">修改密码</h3>
      <el-form :model="pwdForm" label-width="80px">
        <el-form-item label="原密码"><el-input v-model="pwdForm.oldPassword" type="password" show-password /></el-form-item>
        <el-form-item label="新密码"><el-input v-model="pwdForm.newPassword" type="password" show-password /></el-form-item>
        <el-form-item><el-button type="primary" @click="changePassword">提交</el-button></el-form-item>
      </el-form>
    </div>
  </div>
</template>
<script setup lang="ts">
import { reactive, onMounted } from 'vue'
import { useUserStore } from '@/stores/user'
import authApi from '@/api/modules/auth'
import { ElMessage } from 'element-plus'
const userStore = useUserStore()
const form = reactive({ nickname: '', email: '', phone: '' })
const pwdForm = reactive({ oldPassword: '', newPassword: '' })
onMounted(() => {
  const u = userStore.userInfo
  if (u) { form.nickname = u.nickname || ''; form.email = u.email || ''; form.phone = u.phone || '' }
})
async function saveProfile() {
  await authApi.updateProfile(form)
  await userStore.getUserInfo()
  ElMessage.success('保存成功')
}
async function changePassword() {
  await authApi.changePassword(pwdForm)
  ElMessage.success('密码修改成功，请重新登录')
}
</script>
