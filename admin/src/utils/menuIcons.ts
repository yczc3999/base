import {
  LayoutDashboard, FolderOpen, TrendingUp, Settings, Settings2,
  Users, Shield, Menu, Sliders, FileText, BookMarked, User, Timer,
  FolderOpen as FolderOpened, Files, Activity, Link2, Database, Trash2,
  LineChart, FileBarChart, Map, Monitor, MessageSquare, Bell,
  CreditCard, Sparkles, Search, LogIn, BookOpen, type LucideIcon,
} from 'lucide-vue-next'

/**
 * 菜单 icon 映射：menus.icon（DB 存的 Element Plus 图标名 / slug）→ lucide 组件。
 * Sidebar 与 TagsView 共用，保证侧栏 / 页签图标一致。
 */
export const menuIconMap: Record<string, LucideIcon> = {
  // 顶层
  LayoutDashboard,          // 工作台
  FolderOpen,               // 内容管理
  DataLine: TrendingUp,     // SEO 优化 (Element 名 → lucide TrendingUp)
  Setting: Settings2,       // 系统设置 (Element Setting → lucide Settings2)
  Settings,                 // 系统管理
  // 系统管理子项
  Users,                    // 用户管理
  UserCircle: User,         // 前端用户（UserCircle → User）
  Shield,                   // 角色管理
  Menu,                     // 菜单管理
  Sliders,                  // 系统配置
  FileText,                 // 日志管理 / 文章管理
  Collection: BookMarked,   // 数据字典
  User,                     // 前端用户
  Timer,                    // 任务监控
  FolderOpened,             // 数据库备份 / 存储服务
  Files,                    // Migration 管理
  Activity,                 // 操作日志
  LogIn,                    // 登录日志
  Connection: Link2,        // 在线会话
  Coin: Database,           // 缓存管理
  Delete: Trash2,           // 回收站
  // SEO 子项
  TrendCharts: LineChart,   // SEO 概览
  Document: FileBarChart,   // 发布日志
  MapLocation: Map,         // 站点地图
  BookOpen,                 // 组件文档
  // 设置子项
  Monitor,                  // 站点设置
  ChatDotRound: MessageSquare, // 短信服务
  Bell,                     // 通知服务
  CreditCard,               // 支付服务
  MagicStick: Sparkles,     // AI 配置
  Search,                   // SEO 配置 / 关键词管理
}

const fallbackIcon: LucideIcon = FileText

/** 按 slug 取图标组件；未知 slug 用文件图标兜底（保证永不空白） */
export function getMenuIcon(slug?: string | null): LucideIcon {
  if (!slug) return fallbackIcon
  return menuIconMap[slug] || fallbackIcon
}
