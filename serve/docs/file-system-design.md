# 文件系统设计文档

## 一、核心问题

### 1.1 URL 漂移问题

用户在阿里云 OSS 上传了一张图片，URL 是 `https://bucket.oss-cn-beijing.aliyuncs.com/uploads/avatar.jpg`。
后来切换到腾讯云 COS，这个 URL 就废了。

**解决方案：数据库存相对路径，URL 运行时拼接。**

```
files 表存储:
    path = "uploads/2026/04/02/abc.jpg"       ← 相对路径，永远不变
    storage = "aliyun_oss"                     ← 上传时用的哪个存储

读取时:
    if 当前 storage 配置 == file.storage:
        → 直接拼 URL（当前配置的 domain/endpoint）
    else:
        → 用 file.storage 对应的配置拼 URL（旧存储的 domain）
        → 或者走代理 /api/file/{id}（后端读取并转发）
```

### 1.2 隐私文件

`is_private=True` 的文件：
- **强制存本地**，不管 storage 配置的 default 是什么
- 不能通过公网 URL 直接访问
- 读取走后端代理：`GET /api/file/{id}?token=xxx`
- 后端校验权限后读文件内容返回

---

## 二、数据库设计

### files 表

```sql
CREATE TABLE files (
    id          SERIAL PRIMARY KEY,
    name        VARCHAR(200) NOT NULL,         -- 存储文件名（uuid.ext）
    original_name VARCHAR(200) NOT NULL,       -- 原始文件名
    path        VARCHAR(500) NOT NULL,         -- 相对路径（uploads/2026/04/02/xxx.jpg）
    url         VARCHAR(500) NOT NULL,         -- 完整 URL（前端直接用，不运行时拼）
    platform    VARCHAR(50) NOT NULL,          -- 存储平台（local/aliyun_oss/qcloud_cos/...）
    mime_type   VARCHAR(100),                  -- MIME 类型
    size        INTEGER NOT NULL DEFAULT 0,    -- 文件大小（bytes）
    ext         VARCHAR(20),                   -- 扩展名
    is_private  BOOLEAN NOT NULL DEFAULT FALSE,-- 是否隐私文件
    user_id     INTEGER,                       -- 上传者 ID
    category    VARCHAR(50) DEFAULT 'default', -- 分类（avatar/document/export/...）
    created_at  TIMESTAMP NOT NULL DEFAULT now()
);

CREATE INDEX idx_files_user_id ON files (user_id);
CREATE INDEX idx_files_category ON files (category);
CREATE INDEX idx_files_created_at ON files (created_at);
```

---

## 三、后端 API

### 3.1 上传接口

```
POST /api/admin/file/upload
Content-Type: multipart/form-data

参数:
    file:       文件
    category:   分类（默认 "default"）
    is_private: 是否隐私（默认 false）

响应:
{
    "code": 0,
    "data": {
        "id": 1,
        "name": "a1b2c3d4.jpg",
        "original_name": "头像.jpg",
        "url": "https://cdn.example.com/uploads/2026/04/02/a1b2c3d4.jpg",
        "path": "uploads/2026/04/02/a1b2c3d4.jpg",
        "size": 102400,
        "mime_type": "image/jpeg",
        "ext": "jpg"
    }
}
```

流程（FileLogic.upload）：

```python
async def upload(self, db, file, category="default", is_private=False):
    # 1. 校验
    self._validate(file, max_size, allowed_types)

    # 2. 生成路径
    ext = file.filename.rsplit(".", 1)[-1].lower()
    uuid_name = f"{uuid4().hex}.{ext}"
    prefix = "private" if is_private else category
    path = f"{prefix}/{date.today():%Y/%m/%d}/{uuid_name}"

    # 3. 上传到存储
    if is_private:
        # 强制 Local，不管 default 配置
        driver = await storage_service.get_specific_driver(db, "local")
    else:
        # 走 default 配置的存储
        driver = await storage_service.get_driver(db)

    url = await driver.upload(path, content, visible=not is_private)
    if storage_service.failed:
        raise BizError(storage_service.error)

    # 4. 隐私文件 URL 用代理地址
    if is_private:
        url = f"/api/file/{{id}}"  # 写入 DB 后回填

    # 5. 写入 files 表
    record = File(
        name=uuid_name,
        original_name=file.filename,
        path=path,
        url=url,
        platform=storage_service.current_platform,  # 记录用的哪个存储
        mime_type=file.content_type,
        size=len(content),
        ext=ext,
        is_private=is_private,
        user_id=current_user_id,
        category=category,
    )
    db.add(record)
    await db.commit()

    # 6. 隐私文件回填代理 URL
    if is_private:
        record.url = f"/api/file/{record.id}"
        await db.commit()

    return record
```

**关键决策：**
- `is_private=True` → **强制 LocalDriver**，URL 是 `/api/file/{id}` 代理地址
- `is_private=False` → 走 `StorageService.get_driver()`（读 settings 的 default）
- URL **直接存 DB**，前端零计算
- `platform` 字段记录上传时用的存储，永不变

### 3.2 文件列表（文件管理器用）

```
GET /api/admin/file/getList?page=1&pageSize=20&category=avatar&mime_type=image

响应:
{
    "list": [
        {"id": 1, "original_name": "头像.jpg", "url": "...", "size": 102400, "mime_type": "image/jpeg", "created_at": "..."},
        ...
    ],
    "total": 50
}
```

### 3.3 文件 URL

**公开文件：URL 已存在 DB 的 `url` 字段，不需要额外接口。**
前端拿到 file record 直接用 `record.url` 渲染。

**隐私文件：URL 是 `/api/file/{id}`，走代理接口（见 3.4）。**

### 3.4 隐私文件代理

```
GET /api/file/{id}
Authorization: Bearer {token}

响应: 文件内容（Content-Type 按 mime_type，Content-Disposition 按 original_name）
```

流程：
1. 校验 token（必须登录）
2. 查 files 表拿 record
3. 校验 `is_private=True`（公开文件直接用 URL，不走代理）
4. 校验权限（record.user_id == 当前用户 或 超管）
5. 读取本地文件内容
6. 返回文件流

### 3.5 文件删除

```
POST /api/admin/file/doDelete
{"ids": [1, 2, 3]}
```

流程（FileLogic.delete，PHP 版缺的我们补上）：

```python
async def delete_files(self, db, ids: list[int]):
    for file_id in ids:
        record = await self.get_detail(db, file_id)
        if not record:
            continue

        # 1. 删存储文件（按 record.platform 获取对应 driver）
        try:
            driver = await storage_service.get_specific_driver(db, record["platform"])
            if driver:
                await driver.delete(record["path"])
        except Exception:
            pass  # 存储删除失败不阻塞，记日志

        # 2. 删数据库记录
        await db.execute(delete(File).where(File.id == file_id))

    await db.commit()
```

**关键：用 `record.platform`（不是当前 default）获取 driver 去删对应存储的文件。**

---

## 四、前端组件设计

### 4.1 拆分原则

**两个独立组件，不互相依赖：**

| 组件 | 场景 | 预览方式 |
|------|------|---------|
| `ImageUpload` | 头像、封面、截图 | 缩略图 / 裁剪 |
| `FileUpload` | 文档、Excel、压缩包 | 文件列表 + 图标 |

**一个共享弹窗：**

| 组件 | 说明 |
|------|------|
| `FileManager` | 文件管理器弹窗（网格/列表视图，搜索，选择） |

### 4.2 ImageUpload

```vue
<ImageUpload
  v-model="form.avatar"
  accept="image/jpeg,image/png,image/webp"
  :max-size="2"           <!-- MB -->
  :max-width="1920"       <!-- 像素，0=不限 -->
  :max-height="1080"
  :multiple="false"
  :limit="1"              <!-- 多图时最大数量 -->
  :private="false"
  :preview="true"         <!-- 是否显示预览 -->
  :cropper="false"        <!-- 是否开启裁剪 -->
  :aspect-ratio="1"       <!-- 裁剪比例，0=自由 -->
  category="avatar"       <!-- 分类 -->
  :browsable="true"       <!-- 是否可打开文件管理器选择已有图片 -->
/>
```

**外观：**
- 单图：一个方形区域，有图显示缩略图 + 删除/替换按钮，无图显示 + 图标
- 多图：网格排列，最后一个是 + 按钮（未达 limit 时）
- 拖拽上传
- 点击上传（打开文件选择器）
- 可选：点击"从文件库选择"→ 打开 FileManager

**v-model 值：**
- 单图：`string`（URL 或文件 ID）
- 多图：`string[]`

### 4.3 FileUpload

```vue
<FileUpload
  v-model="form.attachments"
  accept=".pdf,.docx,.xlsx,.zip"
  :max-size="50"          <!-- MB -->
  :multiple="true"
  :limit="5"
  :private="false"
  category="document"
  :browsable="true"
/>
```

**外观：**
- 拖拽区域 + 文件列表
- 每个文件项：图标 + 文件名 + 大小 + 进度条（上传中）+ 删除按钮
- 列表模式（不是网格）

**v-model 值：**
- 单文件：`string`
- 多文件：`string[]`

### 4.4 FileManager（文件管理器弹窗）

```vue
<FileManager
  v-model:visible="showManager"
  :accept="'image/*'"       <!-- 过滤类型 -->
  :multiple="true"
  :limit="5"
  :category="'avatar'"
  @select="handleSelect"    <!-- 选中回调，返回文件对象数组 -->
/>
```

**布局：**
```
┌──────────────────────────────────────────────────┐
│ 文件管理器                              [✕ 关闭] │
├──────────────────────────────────────────────────┤
│ [上传文件] [网格视图|列表视图]  🔍 搜索文件名     │
│ [全部] [图片] [文档] [视频] [其他]    ← 类型筛选  │
├──────────────────────────────────────────────────┤
│ ┌─────┐ ┌─────┐ ┌─────┐ ┌─────┐ ┌─────┐        │
│ │ ☑   │ │     │ │     │ │ ☑   │ │     │        │
│ │ 缩略 │ │ 缩略 │ │ 缩略 │ │ 缩略 │ │ 缩略 │        │
│ │ 图   │ │ 图   │ │ 图   │ │ 图   │ │ 图   │        │
│ ├─────┤ ├─────┤ ├─────┤ ├─────┤ ├─────┤        │
│ │名称  │ │名称  │ │名称  │ │名称  │ │名称  │        │
│ └─────┘ └─────┘ └─────┘ └─────┘ └─────┘        │
│                                                  │
│ ◀ 1 2 3 ▶                                       │
├──────────────────────────────────────────────────┤
│                    已选 2/5        [取消] [确定]  │
└──────────────────────────────────────────────────┘
```

**功能：**
- 上传：顶部有上传按钮，上传后自动出现在列表中
- 视图切换：网格（缩略图）/ 列表（文件名+大小+时间）
- 搜索：按文件名搜索
- 类型筛选：全部 / 图片 / 文档 / 视频 / 其他
- 选择：单击选中（高亮边框 + 勾选角标），已选数量显示
- 分页：底部分页
- 限制：超过 limit 数量后不能继续选

---

## 五、交互流程

### 5.1 直接上传

```
用户点击 ImageUpload 的 + 号
  → 系统文件选择器弹出
  → 选择文件
  → 前端校验（大小/类型/尺寸）
  → 上传到 /api/admin/file/upload
  → 显示进度条
  → 完成后显示缩略图
  → v-model 更新为 URL 或 ID
```

### 5.2 从文件库选择

```
用户点击"从文件库选择"
  → FileManager 弹窗打开
  → 显示已上传的文件（按 category 过滤）
  → 用户可以在弹窗内上传新文件
  → 用户勾选文件
  → 点击确定
  → v-model 更新
  → FileManager 关闭
```

### 5.3 隐私文件上传

```
FileUpload/ImageUpload 设置 :private="true"
  → 上传时 is_private=true 传给后端
  → 后端强制走 LocalDriver
  → 存储在 private/ 目录（无公网 URL）
  → 返回的 url 是代理地址：/api/file/1?token=xxx
  → 前端显示时通过代理地址访问
```

---

## 六、URL 策略

| 文件类型 | URL 格式 | 访问方式 |
|---------|---------|---------|
| 公开图片 | `https://cdn.example.com/uploads/2026/04/02/abc.jpg` | 直接访问 |
| 公开文件 | 同上 | 直接下载 |
| 隐私文件 | `/api/file/{id}` | 后端代理（校验权限） |
| 存储切换后的旧文件 | 用旧存储的 URL 配置拼 | 仍然可访问 |

**files 表只存 `path`（相对路径）+ `storage`（驱动名），不存完整 URL。**

URL 生成逻辑：
```python
def get_file_url(file_record, current_storage_config):
    if file_record.is_private:
        return f"/api/file/{file_record.id}"

    # 用文件当时的存储驱动的配置拼 URL
    driver_config = current_storage_config.get(file_record.storage, {})
    domain = driver_config.get("domain", "")
    if domain:
        return f"{domain}/{file_record.path}"

    # 降级：用当前默认存储拼
    return f"/{file_record.path}"
```

---

## 七、影响范围

### 新增

| 文件 | 说明 |
|------|------|
| **后端** | |
| `databases/migrations/014_create_files.sql` | 文件表 |
| `app/models/file.py` | File Model |
| `app/logics/file.py` | FileLogic（上传/URL生成/删除） |
| `app/controllers/admin/file.py` | 文件上传/列表/删除接口 |
| **前端** | |
| `src/components/ImageUpload/index.vue` | 图片上传组件 |
| `src/components/FileUpload/index.vue` | 文件上传组件 |
| `src/components/FileManager/index.vue` | 文件管理器弹窗 |
| `src/api/modules/file.ts` | 文件 API |

### 修改

| 文件 | 改动 |
|------|------|
| `app/models/__init__.py` | 导出 File Model |
| `app/main.py` | 注册文件路由 |
| 用户管理编辑表单 | 头像字段用 ImageUpload |
