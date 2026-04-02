<template>
  <div>
    <h2 style="font-size:18px;font-weight:600;margin-bottom:20px">存储配置</h2>
    <SettingForm category="storage" :providers="providers" mode="single" />
  </div>
</template>

<script setup lang="ts">
import SettingForm from '@/components/SettingForm/index.vue'
import type { SettingProvider } from '@/components/SettingForm/types'

const providers: SettingProvider[] = [
  {
    key: 'local', label: '本地存储', icon: '💾', desc: '服务器本地磁盘',
    fields: [
      { name: 'path', label: '存储路径', placeholder: '/uploads', required: true },
      { name: 'domain', label: '访问域名', placeholder: 'https://cdn.example.com', tip: '文件访问的域名前缀' },
    ],
  },
  {
    key: 'aliyun_oss', label: '阿里云 OSS', icon: '☁',
    fields: [
      { name: 'access_key_id', label: 'AccessKey ID', required: true },
      { name: 'access_key_secret', label: 'AccessKey Secret', type: 'password', required: true },
      { name: 'bucket', label: 'Bucket', required: true },
      { name: 'endpoint', label: 'Endpoint', required: true, placeholder: 'oss-cn-hangzhou.aliyuncs.com' },
      { name: 'domain', label: '自定义域名', tip: '绑定的 CDN 域名，不填则用默认域名' },
    ],
  },
  {
    key: 'qcloud_cos', label: '腾讯云 COS', icon: '🐧',
    fields: [
      { name: 'secret_id', label: 'SecretId', required: true },
      { name: 'secret_key', label: 'SecretKey', type: 'password', required: true },
      { name: 'bucket', label: 'Bucket', required: true },
      { name: 'region', label: 'Region', required: true, placeholder: 'ap-guangzhou' },
      { name: 'domain', label: '自定义域名' },
    ],
  },
  {
    key: 'qiniu', label: '七牛云', icon: '💎',
    fields: [
      { name: 'access_key', label: 'AccessKey', required: true },
      { name: 'secret_key', label: 'SecretKey', type: 'password', required: true },
      { name: 'bucket', label: 'Bucket', required: true },
      { name: 'domain', label: '访问域名', required: true },
    ],
  },
  {
    key: 's3', label: 'AWS S3 / 兼容', icon: '📦', desc: '支持 AWS S3 及兼容存储（MinIO、R2 等）',
    fields: [
      { name: 'access_key', label: 'Access Key', required: true },
      { name: 'secret_key', label: 'Secret Key', type: 'password', required: true },
      { name: 'bucket', label: 'Bucket', required: true },
      { name: 'region', label: 'Region', placeholder: 'us-east-1' },
      { name: 'endpoint', label: 'Endpoint', tip: '自定义端点（MinIO / R2 等）' },
      { name: 'domain', label: '访问域名' },
    ],
  },
]
</script>
