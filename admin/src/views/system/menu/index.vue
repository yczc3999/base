<template>
  <div>
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
      <div><el-button type="primary" :icon="Plus" @click="handleAdd">新增</el-button></div>
      <div><el-button :icon="Refresh" @click="loadTree">刷新</el-button></div>
    </div>
    <div style="background:var(--bg-card);border:1px solid var(--border);border-radius:var(--radius);overflow:hidden">
      <el-table v-loading="loading" :data="treeData" row-key="id" border default-expand-all :tree-props="{ children: 'children' }">
        <el-table-column prop="label" label="名称" width="130" />
        <el-table-column prop="slug" label="标识" width="140" />
        <el-table-column prop="type" label="类型" width="80" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.type===0" size="small">目录</el-tag>
            <el-tag v-else-if="row.type===1" type="success" size="small">菜单</el-tag>
            <el-tag v-else type="warning" size="small">按钮</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="icon" label="图标" width="60" align="center">
          <template #default="{ row }">
            <span v-if="row.icon" :title="row.icon">{{ menuIconMap[row.icon] || row.icon }}</span>
            <span v-else style="color:#CBD5E1">—</span>
          </template>
        </el-table-column>
        <el-table-column prop="perms" label="权限标识" width="180" />
        <el-table-column prop="sort" label="排序" width="70" align="center" />
        <el-table-column prop="status" label="状态" width="80" align="center">
          <template #default="{ row }">
            <span class="status-badge" :class="row.status===1?'success':'danger'">{{ row.status===1?'正常':'禁用' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="180" fixed="right" align="center">
          <template #default="{ row }">
            <el-button type="primary" link size="small" :icon="Edit" @click="handleEdit(row)">编辑</el-button>
            <el-button type="danger" link size="small" :icon="Delete" @click="handleDelete(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>
    <el-dialog v-model="formVisible" :title="formMode==='create'?'新增菜单':'编辑菜单'" width="560px" destroy-on-close>
      <el-form ref="formRef" :model="formData" :rules="rules" label-width="100px">
        <el-form-item label="类型" prop="type">
          <el-radio-group v-model="formData.type">
            <el-radio :value="0">目录</el-radio>
            <el-radio :value="1">菜单</el-radio>
            <el-radio :value="2">按钮</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="父级" prop="parent_id">
          <el-tree-select v-model="formData.parent_id" :data="parentOptions" node-key="id" :props="{ label:'label', children:'children' }" check-strictly default-expand-all placeholder="顶级" clearable style="width:100%" />
        </el-form-item>
        <el-form-item label="标识" prop="slug"><el-input v-model="formData.slug" /></el-form-item>
        <el-form-item label="名称" prop="label"><el-input v-model="formData.label" /></el-form-item>
        <el-form-item v-if="formData.type!==2" label="图标"><IconPicker v-model="formData.icon" /></el-form-item>
        <el-form-item v-if="formData.type===1" label="路由"><el-input v-model="formData.path" /></el-form-item>
        <el-form-item v-if="formData.type===1" label="组件"><el-input v-model="formData.template_path" /></el-form-item>
        <el-form-item v-if="formData.type===2" label="权限标识"><el-input v-model="formData.perms" /></el-form-item>
        <el-form-item label="排序"><el-input-number v-model="formData.sort" :min="0" /></el-form-item>
        <el-form-item label="状态"><el-switch v-model="formData.status" :active-value="1" :inactive-value="0" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="formVisible=false">取消</el-button>
        <el-button type="primary" :icon="Check" :loading="submitLoading" @click="handleSubmit">确 定</el-button>
      </template>
    </el-dialog>
  </div>
</template>
<script setup lang="ts">
defineOptions({ name: 'SystemMenu' })
import { ref, computed, onMounted } from 'vue'
import { Plus, Edit, Delete, Check, RefreshRight as Refresh } from "@element-plus/icons-vue"
import IconPicker from "@/components/IconPicker/index.vue"
import menuApi from '@/api/modules/menu'
import { ElMessage } from 'element-plus/es/components/message/index'
import { confirmDialog } from '@/utils/confirm'
const menuIconMap: Record<string, string> = {
  Settings: '⚙', Users: '👤', Shield: '🛡', Menu: '☰',
  Sliders: '⊞', FileText: '📄', Activity: '◉', LogIn: '→',
  LayoutDashboard: '▦', UserCircle: '◎', Bell: '🔔',
  Globe: '🌐', MessageSquare: '💬', HardDrive: '💾',
  CreditCard: '💳', Mail: '✉',
}
const loading = ref(false)
const treeData = ref<any[]>([])
const formVisible = ref(false)
const formMode = ref<'create'|'edit'>('create')
const formData = ref<any>({ type: 1, parent_id: 0, sort: 0, status: 1 })
const submitLoading = ref(false)
const formRef = ref()
const rules = { slug: [{ required: true, message: '请输入标识' }], label: [{ required: true, message: '请输入名称' }] }
const parentOptions = computed(() => [{ id: 0, label: '顶级', children: treeData.value }])
async function loadTree() {
  loading.value = true
  try { treeData.value = await menuApi.tree() } catch { /* 菜单树加载失败静默 */ }
  loading.value = false
}
function handleAdd() { formMode.value='create'; formData.value={ type:1, parent_id:0, sort:0, status:1 }; formVisible.value=true }
async function handleEdit(row:any) {
  formMode.value='edit'
  try { formData.value={ ...(await menuApi.getDetail(row.id)) } } catch { formData.value={...row} }
  formVisible.value=true
}
async function handleDelete(row:any) {
  await confirmDialog('确定删除此菜单？','提示',{type:'warning'})
  await menuApi.doDelete(row.id)
  ElMessage.success('删除成功')
  loadTree()
}
async function handleSubmit() {
  const valid = await formRef.value?.validate().catch(()=>false)
  if(!valid) return
  submitLoading.value=true
  try { await menuApi.doEdit(formData.value); ElMessage.success('操作成功'); formVisible.value=false; loadTree() } catch { /* 保存失败静默处理 */ }
  submitLoading.value=false
}
onMounted(loadTree)
</script>
<style scoped>
.status-badge { display:inline-flex;align-items:center;padding:2px 8px;font-size:12px;font-weight:500;border-radius:2px }
.status-badge.success { background:#DCFCE7;color:#16A34A }
.status-badge.danger { background:#FEE2E2;color:#EF4444 }
</style>
