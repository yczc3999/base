<template>
  <div>
    <CrudTable ref="crudRef" api="admin/client_user" perms="admin:client_user"
      :columns="columns" :search-fields="searchFields" :form-fields="formFields"
      :action-width="260">
      <template #actions="{ row }">
        <el-button type="primary" link size="small" :icon="EditIcon"
          @click="crudRef?.crud.handleEdit(row)">编辑</el-button>
        <el-button type="warning" link size="small" :icon="Key"
          @click="openResetPwd(row)">重置密码</el-button>
        <el-button type="danger" link size="small" :icon="SwitchButton"
          @click="kick(row)">踢下线</el-button>
        <el-button type="danger" link size="small" :icon="DeleteIcon"
          @click="crudRef?.crud.handleDelete(row)">删除</el-button>
      </template>
    </CrudTable>

    <!-- 重置密码弹窗 -->
    <el-dialog v-model="pwdVisible" :title="`重置密码 — ${currentUser?.username || ''}`"
      width="440px" destroy-on-close>
      <el-form label-width="90px">
        <el-form-item label="新密码" :validate-status="pwdError ? 'error' : ''"
          :error="pwdError">
          <el-input v-model="pwdForm.password" type="password" show-password
            placeholder="至少 6 位" @input="pwdError = ''" />
        </el-form-item>
        <el-form-item label="确认密码">
          <el-input v-model="pwdForm.confirm" type="password" show-password
            placeholder="再次输入" />
        </el-form-item>
      </el-form>
      <div class="pwd-tip">⚠️ 重置后该用户所有设备将强制下线，需用新密码重新登录。</div>
      <template #footer>
        <el-button @click="pwdVisible = false">取消</el-button>
        <el-button type="primary" :loading="pwdLoading" @click="confirmResetPwd">确认重置</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Edit as EditIcon, Delete as DeleteIcon, Key, SwitchButton } from '@element-plus/icons-vue'
import CrudTable from '@/components/CrudTable/index.vue'
import type { CrudColumn, SearchField, FormField } from '@/components/CrudTable/types'
import { post } from '@/api/request'

const crudRef = ref()

const columns: CrudColumn[] = [
  { field: 'id', label: 'ID', width: 60 },
  { field: 'username', label: '用户名', minWidth: 120 },
  { field: 'nickname', label: '昵称', minWidth: 100 },
  { field: 'email', label: '邮箱', minWidth: 140 },
  { field: 'phone', label: '手机', width: 120 },
  { field: 'status', label: '状态', width: 80, align: 'center', type: 'switch' },
  { field: 'last_login_at', label: '最后登录', width: 160, type: 'time' },
  { field: 'created_at', label: '创建时间', width: 160, type: 'time' },
]

const searchFields: SearchField[] = [
  { field: 'keyword', label: '搜索', type: 'input', placeholder: '用户名 / 昵称 / 邮箱 / 手机' },
  { field: 'status', label: '状态', type: 'select', options: [
    { label: '正常', value: 1 }, { label: '禁用', value: 0 },
  ] },
]

const formFields: FormField[] = [
  { field: 'username', label: '用户名', showOnCreate: true,
    rules: [{ required: true, message: '请输入用户名' }, { min: 3, max: 20, message: '3-20 位字母数字' }] },
  { field: 'password', label: '密码', type: 'password', showOnCreate: true,
    rules: [{ required: true, message: '请输入密码' }, { min: 6, message: '至少 6 位' }] },
  { field: 'nickname', label: '昵称' },
  { field: 'email', label: '邮箱' },
  { field: 'phone', label: '手机' },
  { field: 'status', label: '状态', type: 'switch', default: 1, options: [{ value: 1, label: '正常' }] },
]

// ---- 重置密码 ----

const pwdVisible = ref(false)
const pwdLoading = ref(false)
const pwdError = ref('')
const currentUser = ref<any>(null)
const pwdForm = reactive({ password: '', confirm: '' })

function openResetPwd(row: any) {
  currentUser.value = row
  pwdForm.password = ''
  pwdForm.confirm = ''
  pwdError.value = ''
  pwdVisible.value = true
}

async function confirmResetPwd() {
  if (!pwdForm.password || pwdForm.password.length < 6) {
    pwdError.value = '密码至少 6 位'
    return
  }
  if (pwdForm.password !== pwdForm.confirm) {
    pwdError.value = '两次输入的密码不一致'
    return
  }
  pwdLoading.value = true
  try {
    await post('/admin/client_user/resetPassword', {
      id: currentUser.value.id, password: pwdForm.password,
    })
    ElMessage.success('密码已重置，用户已强制下线')
    pwdVisible.value = false
  } catch {} finally { pwdLoading.value = false }
}

// ---- 踢下线 ----

async function kick(row: any) {
  await ElMessageBox.confirm(
    `确认将用户「${row.username}」踢下线？该用户所有设备将立即退出登录。`,
    '踢下线', { type: 'warning' },
  )
  await post('/admin/client_user/kick', { id: row.id })
  ElMessage.success('已踢下线')
}
</script>

<style scoped>
.pwd-tip {
  font-size: 12px;
  color: var(--el-color-warning);
  margin-top: 4px;
}
</style>
