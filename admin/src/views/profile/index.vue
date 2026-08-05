<template>
  <div style="max-width:600px">
    <h2 style="font-size:18px;font-weight:600;margin-bottom:20px">个人中心</h2>
    <div style="background:var(--bg-card);border:1px solid var(--border);border-radius:var(--radius);padding:24px">
      <el-form :model="form" label-width="80px">
        <el-form-item label="用户名"><el-input :model-value="userStore.userInfo?.username" disabled /></el-form-item>
        <el-form-item label="昵称"><el-input v-model="form.nickname" /></el-form-item>
        <el-form-item label="邮箱"><el-input v-model="form.email" /></el-form-item>
        <el-form-item label="手机号"><el-input v-model="form.phone" /></el-form-item>
        <el-form-item><el-button type="primary" :icon="Check" @click="saveProfile">保存</el-button></el-form-item>
      </el-form>
    </div>
    <div style="background:var(--bg-card);border:1px solid var(--border);border-radius:var(--radius);padding:24px;margin-top:16px">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">
        <h3 style="font-size:16px;font-weight:600">修改密码</h3>
        <el-button v-if="!showPwdForm" type="primary" link @click="showPwdForm = true">修改</el-button>
      </div>
      <el-form v-if="showPwdForm" :model="pwdForm" label-width="80px">
        <el-form-item label="原密码">
          <el-input ref="oldPwdRef" v-model="pwdForm.oldPassword" type="password" show-password autocomplete="off" />
        </el-form-item>
        <el-form-item label="新密码">
          <el-input v-model="pwdForm.newPassword" type="password" show-password autocomplete="off" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :icon="Check" @click="changePassword">提交</el-button>
          <el-button @click="showPwdForm = false; pwdForm.oldPassword = ''; pwdForm.newPassword = ''">取消</el-button>
        </el-form-item>
      </el-form>
    </div>
  </div>
</template>
<script setup lang="ts">
import { Check } from "@element-plus/icons-vue"
import { useUserStore } from '@/stores/user'
import { usePermissionStore } from '@/stores/permission'
import authApi from '@/api/modules/auth'
import { ElMessage } from 'element-plus'
import { useRouter } from 'vue-router'
const userStore = useUserStore()
const router = useRouter()
const form = reactive({ nickname: '', email: '', phone: '' })
const pwdForm = reactive({ oldPassword: '', newPassword: '' })
const showPwdForm = ref(false)
const oldPwdRef = ref()

// 展开密码表单后, 等一帧清掉 Chrome 自动填充
watch(showPwdForm, (v) => {
  if (v) {
    nextTick(() => {
      setTimeout(() => {
        pwdForm.oldPassword = ''
        pwdForm.newPassword = ''
      }, 50)
    })
  }
})

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
  if (!pwdForm.oldPassword || !pwdForm.newPassword) {
    ElMessage.warning('请填写原密码和新密码')
    return
  }
  await authApi.changePassword(pwdForm)
  ElMessage.success('密码修改成功，即将跳转登录页')
  setTimeout(async () => {
    await userStore.logout()
    usePermissionStore().resetState()
    router.replace('/login')
  }, 800)
}
</script>
