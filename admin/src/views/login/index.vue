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
        <!-- 语言切换 -->
        <div class="login-lang">
          <el-button link size="small" :type="locale === 'zh-CN' ? 'primary' : 'default'" @click="setLocale('zh-CN')">中</el-button>
          <span class="lang-divider">/</span>
          <el-button link size="small" :type="locale === 'en-US' ? 'primary' : 'default'" @click="setLocale('en-US')">EN</el-button>
        </div>

        <h2 class="form-title">{{ $t('login.title') }}</h2>
        <p class="form-desc">{{ $t('login.desc') }}</p>

        <el-form ref="formRef" :model="form" :rules="rules" @submit.prevent="handleLogin" class="login-form">
          <el-form-item prop="username">
            <el-input
              v-model="form.username"
              :placeholder="$t('login.username')"
              size="large"
              :prefix-icon="UserIcon"
            />
          </el-form-item>

          <el-form-item prop="password">
            <el-input
              v-model="form.password"
              type="password"
              :placeholder="$t('login.password')"
              size="large"
              show-password
              :prefix-icon="LockIcon"
              @keyup.enter="handleLogin"
            />
          </el-form-item>

          <!-- 验证码 -->
          <el-form-item prop="captchaCode" v-if="captchaVisible">
            <div class="captcha-row">
              <el-input
                v-model="form.captchaCode"
                :placeholder="$t('login.captcha')"
                size="large"
                :prefix-icon="KeyIcon"
                maxlength="4"
                @keyup.enter="handleLogin"
              />
              <div class="captcha-svg" v-html="captchaSvg" :title="$t('common.refresh')" @click="loadCaptcha" />
            </div>
          </el-form-item>

          <div class="form-options">
            <el-checkbox v-model="form.remember">{{ $t('login.remember') }}</el-checkbox>
          </div>

          <el-button
            type="primary"
            size="large"
            :loading="loading"
            class="login-btn"
            @click="handleLogin"
          >
            {{ loading ? $t('login.loggingIn') : $t('login.submit') }}
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
import { useI18n } from 'vue-i18n'
import { useSiteStore } from '@/stores/site'
import { useUserStore } from '@/stores/user'
import { get } from '@/api/request'
import { saveLocale } from '@/locales'
import { User as UserIcon, Lock as LockIcon, Key as KeyIcon } from '@element-plus/icons-vue'

const router = useRouter()
const route = useRoute()
const userStore = useUserStore()
const siteStore = useSiteStore()
const { locale } = useI18n()
const formRef = ref()
const loading = ref(false)

function setLocale(lang: 'zh-CN' | 'en-US') {
  locale.value = lang
  saveLocale(lang)
}

onMounted(() => {
  siteStore.load()
  loadCaptcha()
})

const form = reactive({
  username: '',
  password: '',
  remember: false,
  captchaId: '',
  captchaCode: '',
})

// ---- 验证码（P2-1）----
const captchaVisible = ref(true)
const captchaSvg = ref('')

async function loadCaptcha() {
  try {
    const data = await get('/admin/user/captcha', {}, { showError: false })
    form.captchaId = data.captcha_id
    captchaSvg.value = data.svg
    form.captchaCode = ''
  } catch {
    captchaVisible.value = false  // 验证码服务不可用时降级, 不阻塞登录
  }
}

const rules = {
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
  captchaCode: [{ required: true, message: '请输入验证码', trigger: 'blur' }],
}

async function handleLogin() {
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) return

  loading.value = true
  try {
    await userStore.login(form.username, form.password, {
      captcha_id: form.captchaId,
      captcha_code: form.captchaCode,
    })
    const redirect = (route.query.redirect as string) || '/dashboard'
    router.push(redirect)
  } catch {
    // 验证码一次性, 登录失败后刷新
    loadCaptcha()
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

.login-lang {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  margin-bottom: 8px;

  .lang-divider { font-size: 12px; color: var(--text-placeholder); }
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

.captcha-row {
  display: flex;
  gap: 8px;
  align-items: center;
  width: 100%;

  .el-input { flex: 1; }
  .captcha-svg {
    width: 120px; height: 40px;
    cursor: pointer;
    border: 1px solid var(--border);
    border-radius: var(--radius);
    overflow: hidden;
    flex-shrink: 0;

    svg { display: block; }
  }
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
