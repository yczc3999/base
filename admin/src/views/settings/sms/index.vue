<template>
  <div>
    <h2 style="font-size:18px;font-weight:600;margin-bottom:20px">短信配置</h2>
    <SettingForm category="sms" :providers="providers" mode="single" />
  </div>
</template>

<script setup lang="ts">
defineOptions({ name: 'SettingsSms' })
import SettingForm from '@/components/SettingForm/index.vue'
import type { SettingProvider } from '@/components/SettingForm/types'

const providers: SettingProvider[] = [
  {
    key: 'aliyun', label: '阿里云短信', icon: '☁', desc: '阿里云短信服务（国内）',
    fields: [
      { name: 'access_key_id', label: 'AccessKey ID', required: true },
      { name: 'access_key_secret', label: 'AccessKey Secret', type: 'password', required: true },
      { name: 'sign_name', label: '签名名称', required: true, tip: '短信签名，需在阿里云控制台申请' },
      { name: 'template_code', label: '模板 Code', tip: '验证码模板编号' },
    ],
  },
  {
    key: 'aliyun_intl', label: '阿里云国际', icon: '🌏', desc: '阿里云短信服务（国际/港澳台）',
    fields: [
      { name: 'access_key_id', label: 'AccessKey ID', required: true },
      { name: 'access_key_secret', label: 'AccessKey Secret', type: 'password', required: true },
      { name: 'sign_name', label: '签名名称', required: true },
      { name: 'template_code', label: '模板 Code' },
    ],
  },
  {
    key: 'qcloud', label: '腾讯云短信', icon: '🐧', desc: '腾讯云短信（SMS）',
    fields: [
      { name: 'secret_id', label: 'SecretId', required: true },
      { name: 'secret_key', label: 'SecretKey', type: 'password', required: true },
      { name: 'app_id', label: 'SDK AppID', required: true },
      { name: 'sign_name', label: '签名', required: true },
      { name: 'template_id', label: '模板 ID' },
    ],
  },
  {
    key: 'huawei', label: '华为云短信', icon: '📱',
    fields: [
      { name: 'app_key', label: 'APP Key', required: true },
      { name: 'app_secret', label: 'APP Secret', type: 'password', required: true },
      { name: 'sender', label: '通道号', required: true },
      { name: 'template_id', label: '模板 ID' },
      { name: 'signature', label: '签名名称' },
    ],
  },
  {
    key: 'qiniu', label: '七牛云短信', icon: '💎',
    fields: [
      { name: 'access_key', label: 'AccessKey', required: true },
      { name: 'secret_key', label: 'SecretKey', type: 'password', required: true },
      { name: 'template_id', label: '模板 ID' },
    ],
  },
]
</script>
