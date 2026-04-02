<template>
  <div class="login-page">
    <!-- 左侧品牌 -->
    <div class="brand-panel">
      <div class="brand-content">
        <img v-if="siteStore.logo" :src="siteStore.logo" class="brand-logo-img" />
        <div v-else class="brand-logo">{{ siteStore.name?.charAt(0) || 'B' }}</div>
        <h1 class="brand-title">{{ siteStore.name || 'Base Platform' }}</h1>
        <div class="brand-divider"></div>
        <p class="brand-desc">{{ siteStore.description || '企业级应用基础设施' }}</p>
      </div>
      <div class="brand-footer">
        <span class="status-dot"></span>
        <span>v1.0.0 · 系统运行中</span>
      </div>
    </div>

    <!-- 右侧表单 -->
    <div class="form-panel">
      <div class="form-container">
        <h2 class="form-title">登录</h2>
        <p class="form-desc">使用管理员账户登录系统</p>

        <el-form ref="formRef" :model="form" :rules="rules" @submit.prevent="handleLogin" class="login-form">
          <el-form-item prop="username">
            <el-input
              v-model="form.username"
              placeholder="用户名或邮箱"
              size="large"
              :prefix-icon="UserIcon"
            />
          </el-form-item>

          <el-form-item prop="password">
            <el-input
              v-model="form.password"
              type="password"
              placeholder="请输入密码"
              size="large"
              show-password
              :prefix-icon="LockIcon"
              @keyup.enter="handleLogin"
            />
          </el-form-item>

          <div class="form-options">
            <el-checkbox v-model="form.remember">记住我</el-checkbox>
          </div>

          <el-button
            type="primary"
            size="large"
            :loading="loading"
            class="login-btn"
            @click="handleLogin"
          >
            {{ loading ? '登录中...' : '登 录' }}
          </el-button>
        </el-form>

        <p class="form-footer">{{ siteStore.copyright || '© 2026 Base Platform' }}</p>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useSiteStore } from '@/stores/site'
import { useUserStore } from '@/stores/user'
import { User as UserIcon, Lock as LockIcon } from '@element-plus/icons-vue'

const router = useRouter()
const route = useRoute()
const userStore = useUserStore()
const siteStore = useSiteStore()
const formRef = ref()
const loading = ref(false)

onMounted(() => { siteStore.load() })

const form = reactive({
  username: '',
  password: '',
  remember: false,
})

const rules = {
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
}

async function handleLogin() {
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) return

  loading.value = true
  try {
    await userStore.login(form.username, form.password)
    const redirect = (route.query.redirect as string) || '/dashboard'
    router.push(redirect)
  } catch {
    // request.ts 已经处理了错误提示
  } finally {
    loading.value = false
  }
}
</script>

<style scoped lang="scss">
.login-page {
  display: flex;
  height: 100vh;
}

// ── 左侧品牌 ──
.brand-panel {
  width: 42%;
  min-width: 400px;
  background: var(--bg-sidebar);
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  position: relative;
}

.brand-content { text-align: center; }

.brand-logo {
  width: 56px; height: 56px;
  background: var(--primary);
  color: white;
  display: flex; align-items: center; justify-content: center;
  font-size: 24px; font-weight: 700;
  border-radius: var(--radius);
  margin: 0 auto 24px;
}

.brand-logo-img {
  max-width: 180px;
  max-height: 60px;
  margin: 0 auto 24px;
  object-fit: contain;
}

.brand-title {
  font-size: 42px; font-weight: 700;
  color: #F1F5F9;
  letter-spacing: -1px;
  line-height: 1;
}

.brand-sub {
  font-size: 28px; font-weight: 400;
  color: var(--primary-light);
  margin-top: 4px;
}

.brand-divider {
  width: 48px; height: 3px;
  background: var(--primary);
  margin: 24px auto;
}

.brand-desc {
  font-size: 14px;
  color: #64748B;
  letter-spacing: 0.1em;
}

.brand-footer {
  position: absolute;
  bottom: 32px;
  display: flex; align-items: center; gap: 8px;
  font-size: 12px; color: #64748B;

  .status-dot {
    width: 6px; height: 6px;
    background: var(--success);
    border-radius: 50%;
  }
}

// ── 右侧表单 ──
.form-panel {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--bg-card);
}

.form-container {
  width: 100%;
  max-width: 380px;
  padding: 40px;
}

.form-title {
  font-size: var(--text-2xl);
  font-weight: 700;
  color: var(--text-primary);
  margin-bottom: 4px;
}

.form-desc {
  font-size: var(--text-sm);
  color: var(--text-secondary);
  margin-bottom: 32px;
}

.login-form {
  :deep(.el-form-item) { margin-bottom: 20px; }
  :deep(.el-input__wrapper) { height: 44px; }
}

.form-options {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 24px;
}

.login-btn {
  width: 100%;
  height: 44px !important;
  font-size: var(--text-base) !important;
  font-weight: 600 !important;
  letter-spacing: 0.05em;
}

.form-footer {
  margin-top: 32px;
  text-align: center;
  font-size: var(--text-xs);
  color: var(--text-placeholder);
}

@media (max-width: 992px) {
  .brand-panel { display: none; }
}
</style>
