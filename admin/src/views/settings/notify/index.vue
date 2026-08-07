<template>
  <div>
    <h2 class="settings-title">通知配置</h2>
    <SettingForm category="notify" :providers="providers" mode="single" />
  </div>
</template>

<script setup lang="ts">
defineOptions({ name: 'SettingsNotify' })
import SettingForm from '@/components/SettingForm/index.vue'
import type { SettingProvider } from '@/components/SettingForm/types'

const providers: SettingProvider[] = [
  {
    key: 'telegram', label: 'Telegram',
    fields: [
      { name: 'bot_token', label: 'Bot Token', type: 'password', required: true, tip: '通过 @BotFather 创建' },
      { name: 'chat_id', label: 'Chat ID', required: true, tip: '群组或用户 ID' },
    ],
  },
  {
    key: 'dingtalk', label: '钉钉机器人',
    fields: [
      { name: 'webhook', label: 'Webhook URL', required: true, placeholder: 'https://oapi.dingtalk.com/robot/send?access_token=...' },
      { name: 'secret', label: '签名密钥', type: 'password', tip: '加签方式的 Secret' },
    ],
  },
  {
    key: 'feishu', label: '飞书机器人',
    fields: [
      { name: 'webhook', label: 'Webhook URL', required: true },
      { name: 'secret', label: '签名密钥', type: 'password' },
    ],
  },
  {
    key: 'wechat_work', label: '企业微信',
    fields: [
      { name: 'webhook', label: 'Webhook URL', required: true },
    ],
  },
  {
    key: 'email', label: '邮件通知',
    fields: [
      { name: 'smtp_host', label: 'SMTP 服务器', required: true, placeholder: 'smtp.example.com' },
      { name: 'smtp_port', label: '端口', type: 'number', default: 465 },
      { name: 'smtp_user', label: '用户名', required: true },
      { name: 'smtp_pass', label: '密码', type: 'password', required: true },
      { name: 'from_name', label: '发件人名称', placeholder: 'Base Platform' },
      { name: 'from_addr', label: '发件人地址', required: true },
    ],
  },
]
</script>
