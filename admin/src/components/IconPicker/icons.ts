/**
 * 常用图标集（从 lucide 1700+ 中精选）
 *
 * 按使用场景分类，用于 IconPicker 快速选择。
 * 全量图标仍可通过搜索访问。
 */

export const iconCategories: Record<string, string[]> = {
  '常用': [
    'Home', 'Settings', 'User', 'Users', 'Search', 'Bell', 'Mail',
    'Menu', 'X', 'Check', 'Plus', 'Minus', 'Edit', 'Trash2',
    'Eye', 'EyeOff', 'Lock', 'Unlock', 'Star', 'Heart',
  ],
  '导航': [
    'LayoutDashboard', 'LayoutGrid', 'List', 'Grid3x3', 'Columns3',
    'ChevronLeft', 'ChevronRight', 'ChevronDown', 'ChevronUp',
    'ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'ExternalLink',
  ],
  '系统': [
    'Shield', 'ShieldCheck', 'Key', 'Database', 'Server', 'HardDrive',
    'Cpu', 'Monitor', 'Smartphone', 'Globe', 'Wifi', 'Cloud',
    'Terminal', 'Code', 'Bug', 'Zap', 'Activity', 'Gauge',
  ],
  '文件': [
    'File', 'FileText', 'FilePlus', 'FileEdit', 'Folder', 'FolderOpen',
    'FolderPlus', 'Upload', 'Download', 'Paperclip', 'Image', 'Camera',
  ],
  '操作': [
    'Save', 'Copy', 'Clipboard', 'ClipboardCheck', 'Share2', 'Send',
    'RefreshCw', 'RotateCcw', 'Filter', 'SlidersHorizontal', 'Sliders',
    'MoreHorizontal', 'MoreVertical', 'Move', 'GripVertical',
  ],
  '状态': [
    'CheckCircle', 'XCircle', 'AlertCircle', 'AlertTriangle', 'Info',
    'HelpCircle', 'Clock', 'Timer', 'CalendarDays', 'Calendar',
    'Ban', 'CircleDot', 'Loader2', 'Hourglass',
  ],
  '商务': [
    'Building2', 'Briefcase', 'CreditCard', 'Wallet', 'DollarSign',
    'Receipt', 'BarChart3', 'LineChart', 'PieChart', 'TrendingUp',
    'TrendingDown', 'Package', 'ShoppingCart', 'Store', 'Tag', 'Tags',
  ],
  '社交': [
    'MessageSquare', 'MessageCircle', 'AtSign', 'Phone', 'PhoneCall',
    'Video', 'Headphones', 'Mic', 'Volume2', 'VolumeX',
    'ThumbsUp', 'ThumbsDown', 'Smile', 'UserPlus', 'UserMinus',
  ],
}

/** 所有精选图标名（去重扁平列表） */
export const allIcons: string[] = [...new Set(Object.values(iconCategories).flat())]
