# Service 服务层设计文档

## 一、设计目标

为基础平台提供统一的第三方服务抽象层，业务代码不关心底层用的是哪家服务商，只调用统一接口。

核心原则：
- **配置驱动** — 所有服务商凭证存 settings 表，运行时切换，不改代码
- **接口统一** — 同类服务（如短信）不管用阿里云还是腾讯云，调用方式一样
- **驱动可插拔** — 新增一个服务商 = 新增一个 Driver 文件，注册到工厂
- **错误不静默** — 失败要抛异常、要日志，不能 return False 吞掉

---

## 二、架构总览

```
settings 表（category + name）
    ↓
SettingLogic（读配置，带 Redis 缓存）
    ↓
ServiceFactory（根据 default 选择 Driver）
    ↓
Driver Interface（统一契约）
    ↓
具体 Driver（Aliyun / Qcloud / Qiniu / ...）
```

业务代码调用：
```python
from app.services.sms import sms_service

# 不关心底层是阿里云还是腾讯云
await sms_service.send_code(db, "13800138000", "1234")
```

---

## 三、目录结构

```
app/services/
├── base.py                 # BaseService 基类（配置加载 + 驱动工厂 + 错误通知）
├── sms/
│   ├── __init__.py         # SmsService（统一入口）
│   └── drivers/
│       ├── __init__.py
│       ├── aliyun.py       # 阿里云短信
│       ├── aliyun_intl.py  # 阿里云国际
│       ├── qcloud.py       # 腾讯云短信
│       └── huawei.py       # 华为云短信
├── storage/
│   ├── __init__.py         # StorageService（统一入口）
│   └── drivers/
│       ├── __init__.py
│       ├── local.py        # 本地存储
│       ├── aliyun_oss.py   # 阿里云 OSS
│       ├── qcloud_cos.py   # 腾讯云 COS
│       ├── qiniu.py        # 七牛云
│       └── s3.py           # AWS S3 / MinIO / R2
├── notify/
│   ├── __init__.py         # NotifyService（统一入口）
│   └── drivers/
│       ├── __init__.py
│       ├── telegram.py     # Telegram Bot
│       ├── dingtalk.py     # 钉钉机器人
│       ├── feishu.py       # 飞书机器人
│       ├── wechat_work.py  # 企业微信
│       └── email.py        # SMTP 邮件
├── database.py             # 已有（SQLAlchemy 引擎）
└── redis.py                # 已有（Redis 连接）
```

---

## 四、BaseService 基类

```python
class BaseService:
    """
    所有 Service 的基类

    职责：
    1. 从 settings 表加载配置
    2. 根据 default 字段选择 Driver
    3. 提供错误通知能力（通过 NotifyService）
    """

    category: str = ""          # settings 表的 category（如 "sms"）
    driver_map: dict = {}       # 驱动映射 {"aliyun": AliyunDriver, "qcloud": QcloudDriver}

    async def get_config(self, db) -> dict:
        """从 settings 表加载该 category 的全部配置"""
        from app.logics.setting import setting_logic
        return await setting_logic.get_all_by_category(db, self.category)

    async def get_driver(self, db):
        """根据 default 配置获取当前激活的 Driver 实例"""
        config = await self.get_config(db)
        driver_name = config.get("default", "")

        if driver_name not in self.driver_map:
            raise BizError(f"未配置或不支持的服务商: {driver_name}")

        driver_config = config.get(driver_name, {})
        if isinstance(driver_config, str):
            import json
            driver_config = json.loads(driver_config)

        driver_cls = self.driver_map[driver_name]
        return driver_cls(driver_config)

    async def get_specific_driver(self, db, driver_name: str):
        """获取指定名称的 Driver（不走 default）"""
        config = await self.get_config(db)
        driver_config = config.get(driver_name, {})
        if isinstance(driver_config, str):
            import json
            driver_config = json.loads(driver_config)

        driver_cls = self.driver_map.get(driver_name)
        if not driver_cls:
            raise BizError(f"不支持的服务商: {driver_name}")

        return driver_cls(driver_config)
```

---

## 五、SMS 短信服务

### 5.1 接口定义

```python
from abc import ABC, abstractmethod

class SmsDriver(ABC):
    """短信驱动接口"""

    def __init__(self, config: dict):
        self.config = config

    @abstractmethod
    async def send(self, phone: str, template_id: str, params: dict) -> bool:
        """
        发送短信

        :param phone:       手机号
        :param template_id: 模板 ID
        :param params:      模板参数（如 {"code": "1234"}）
        :return:            是否发送成功
        :raises BizError:   发送失败时抛出（不静默）
        """
        ...

    @abstractmethod
    async def send_raw(self, phone: str, content: str) -> bool:
        """发送自定义内容短信（部分平台支持）"""
        ...
```

### 5.2 SmsService 统一入口

```python
class SmsService(BaseService):
    category = "sms"
    driver_map = {
        "aliyun":      AliyunSmsDriver,
        "aliyun_intl": AliyunIntlSmsDriver,
        "qcloud":      QcloudSmsDriver,
        "huawei":      HuaweiSmsDriver,
    }

    async def send_code(self, db, phone: str, code: str):
        """发送验证码（便捷方法）"""
        driver = await self.get_driver(db)
        config = await self.get_config(db)
        default = config.get("default", "")
        driver_config = config.get(default, {})
        template_id = driver_config.get("template_code", "")

        return await driver.send(phone, template_id, {"code": code})

    async def verify_code(self, phone: str, code: str) -> bool:
        """验证验证码（从 Redis 读取比对）"""
        from app.services.redis import cache_get
        cached = await cache_get(f"sms_code:{phone}")
        return cached == code
```

### 5.3 实现原则：纯 HTTP，不用 SDK

**所有驱动一律通过 HTTP API + 手写签名实现，不引入第三方 SDK。**

理由：
- SDK 动辄几十 MB 依赖链，只为调一个接口
- SDK 版本更新可能 break，运维成本高
- 各云厂商的 API 本质就是一个 HTTP 请求 + HMAC 签名
- 签名算法自己实现，几十行代码，永远可控

**工具依赖：**
- HTTP 请求 → 项目已有的 `app/utils/http.py`（Http 工具类）
- HMAC 签名 → Python 标准库 `hmac` + `hashlib`
- 异步请求 → `aiohttp` 或降级 `urllib`（已有降级机制）

### 5.4 驱动实现示例（阿里云短信 — 纯 HTTP）

```python
import hmac, hashlib, base64, time, uuid, json
from urllib.parse import quote

class AliyunSmsDriver(SmsDriver):
    """
    阿里云短信 — 纯 HTTP API 调用

    API 文档：https://help.aliyun.com/document_detail/419298.html
    签名方式：HMAC-SHA1

    config：
    {
        "access_key_id": "...",
        "access_key_secret": "...",
        "sign_name": "...",
        "template_code": "SMS_xxx"
    }
    """

    API_URL = "https://dysmsapi.aliyuncs.com"

    async def send(self, phone: str, template_id: str, params: dict) -> bool:
        template_id = template_id or self.config.get("template_code", "")
        sign_name = self.config["sign_name"]

        query = {
            "PhoneNumbers": phone,
            "SignName": sign_name,
            "TemplateCode": template_id,
            "TemplateParam": json.dumps(params, ensure_ascii=False),
            "Action": "SendSms",
            "Version": "2017-05-25",
            "Format": "JSON",
            "AccessKeyId": self.config["access_key_id"],
            "SignatureMethod": "HMAC-SHA1",
            "Timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "SignatureVersion": "1.0",
            "SignatureNonce": str(uuid.uuid4()),
        }

        # 签名
        query["Signature"] = self._sign(query)

        # 发送
        from app.utils.http import Http
        result = await Http.GET(self.API_URL, params=query)

        if result.get("Code") != "OK":
            raise BizError(f"短信发送失败: {result.get('Message', '未知错误')}")
        return True

    def _sign(self, params: dict) -> str:
        """阿里云 HMAC-SHA1 签名"""
        sorted_params = sorted(params.items())
        query_str = "&".join(f"{quote(k, safe='')}={quote(str(v), safe='')}" for k, v in sorted_params)
        string_to_sign = f"GET&%2F&{quote(query_str, safe='')}"
        key = (self.config["access_key_secret"] + "&").encode()
        signature = base64.b64encode(
            hmac.new(key, string_to_sign.encode(), hashlib.sha1).digest()
        ).decode()
        return signature

    async def send_raw(self, phone: str, content: str) -> bool:
        raise BizError("阿里云短信不支持自定义内容发送")
```

### 5.5 签名算法清单

| 平台 | 签名方式 | 复杂度 |
|------|---------|--------|
| 阿里云 | HMAC-SHA1 + URL 编码排序 | 约 20 行 |
| 腾讯云 | HMAC-SHA256 + TC3-HMAC-SHA256 | 约 40 行 |
| 华为云 | HMAC-SHA256 + WSSE 认证 | 约 30 行 |
| 七牛云 | HMAC-SHA1 + QBox 认证 | 约 15 行 |
| AWS S3 | HMAC-SHA256 + AWS4-HMAC-SHA256 | 约 50 行 |
| Telegram | 无签名，Bearer Token | 0 行 |
| 钉钉 | HMAC-SHA256 + timestamp | 约 10 行 |
| 飞书 | HMAC-SHA256 + timestamp | 约 10 行 |

**每个签名算法都是纯标准库实现（hmac + hashlib），不依赖任何第三方包。**

---

## 六、Storage 存储服务

### 6.1 接口定义

```python
class StorageDriver(ABC):
    """存储驱动接口"""

    def __init__(self, config: dict):
        self.config = config

    @abstractmethod
    async def upload(self, path: str, content: bytes, visible: bool = True) -> str:
        """
        上传文件

        :param path:    存储路径（如 "uploads/2026/04/02/abc.jpg"）
        :param content: 文件内容（bytes）
        :param visible: 是否公开访问
        :return:        文件访问 URL
        :raises BizError: 上传失败时抛出
        """
        ...

    @abstractmethod
    async def delete(self, path: str) -> bool:
        """删除文件"""
        ...

    @abstractmethod
    async def exists(self, path: str) -> bool:
        """判断文件是否存在"""
        ...

    @abstractmethod
    def url(self, path: str) -> str:
        """生成文件访问 URL"""
        ...
```

### 6.2 StorageService 统一入口

```python
class StorageService(BaseService):
    category = "storage"
    driver_map = {
        "local":       LocalStorageDriver,
        "aliyun_oss":  AliyunOssDriver,
        "qcloud_cos":  QcloudCosDriver,
        "qiniu":       QiniuStorageDriver,
        "s3":          S3StorageDriver,
    }

    async def upload_file(self, db, path: str, content: bytes, visible: bool = True) -> str:
        """上传文件（便捷方法，返回 URL）"""
        driver = await self.get_driver(db)
        return await driver.upload(path, content, visible)

    async def delete_file(self, db, path: str) -> bool:
        """删除文件"""
        driver = await self.get_driver(db)
        return await driver.delete(path)

    async def get_url(self, db, path: str) -> str:
        """获取文件 URL"""
        driver = await self.get_driver(db)
        return driver.url(path)
```

### 6.3 URL 生成规则

| 驱动 | URL 格式 |
|------|---------|
| local | `{config.domain}/{path}` |
| aliyun_oss | `https://{bucket}.{endpoint}/{path}` 或 `{config.domain}/{path}` |
| qcloud_cos | `https://{bucket}.cos.{region}.myqcloud.com/{path}` |
| qiniu | `{config.domain}/{path}` |
| s3 | `https://{bucket}.{config.endpoint}/{path}` 或 `{config.domain}/{path}` |

规则：有自定义域名（`config.domain`）优先用自定义域名，没有则拼默认域名。

### 6.4 可见性处理

| 驱动 | visible=True | visible=False |
|------|-------------|--------------|
| local | 存到 `public/` 目录 | 存到 `private/` 目录 |
| aliyun_oss | ACL: public-read | ACL: private |
| qcloud_cos | ACL: public-read | ACL: private |
| qiniu | 公开空间 | 不支持（全公开） |
| s3 | ACL: public-read | ACL: private |

---

## 七、Notify 通知服务

### 7.1 接口定义

```python
class NotifyDriver(ABC):
    """通知驱动接口"""

    def __init__(self, config: dict):
        self.config = config

    @abstractmethod
    async def send(self, title: str, content: str, **kwargs) -> bool:
        """
        发送通知

        :param title:   标题
        :param content: 内容（支持 Markdown）
        :param kwargs:  额外参数（如 at_mobiles、msg_type 等）
        :return:        是否发送成功
        """
        ...
```

### 7.2 NotifyService 统一入口

```python
class NotifyService(BaseService):
    category = "notify"
    driver_map = {
        "telegram":     TelegramDriver,
        "dingtalk":     DingtalkDriver,
        "feishu":       FeishuDriver,
        "wechat_work":  WechatWorkDriver,
        "email":        EmailDriver,
    }

    async def send(self, db, title: str, content: str, **kwargs) -> bool:
        """通过默认渠道发送通知"""
        driver = await self.get_driver(db)
        return await driver.send(title, content, **kwargs)

    async def send_by(self, db, driver_name: str, title: str, content: str, **kwargs) -> bool:
        """通过指定渠道发送通知"""
        driver = await self.get_specific_driver(db, driver_name)
        return await driver.send(title, content, **kwargs)

    async def broadcast(self, db, title: str, content: str, **kwargs) -> dict:
        """通过所有已启用渠道广播（串行逐渠道，每渠道独立状态，如告警通知）"""
        config = await self.get_config(db)
        results = {}
        for name, driver_cls in self.driver_map.items():
            if config.get(f"{name}_enabled") == "1":
                try:
                    driver_config = config.get(name, {})
                    if isinstance(driver_config, str):
                        import json
                        driver_config = json.loads(driver_config)
                    driver = driver_cls(driver_config)
                    results[name] = await driver.send(title, content, **kwargs)
                except Exception:
                    results[name] = False
        return results
```

### 7.3 驱动实现示例（Telegram）

```python
class TelegramDriver(NotifyDriver):
    """
    config 结构：
    {
        "bot_token": "123456:ABC-xxx",
        "chat_id": "-100123456789"
    }
    """

    async def send(self, title: str, content: str, **kwargs) -> bool:
        from app.utils.http import Http

        text = f"*{title}*\n\n{content}"
        url = f"https://api.telegram.org/bot{self.config['bot_token']}/sendMessage"

        result = await Http.POST(url, json={
            "chat_id": self.config["chat_id"],
            "text": text,
            "parse_mode": "Markdown",
        })

        return result.get("ok", False)
```

---

## 八、Settings 表数据结构

```
category    name              value
─────────   ────────────      ──────────────────────────────
sms         default           aliyun
sms         aliyun            {"access_key_id":"...", "access_key_secret":"...", "sign_name":"...", "template_code":"SMS_xxx"}
sms         qcloud            {"secret_id":"...", "secret_key":"...", "app_id":"...", "sign_name":"...", "template_id":"..."}

storage     default           local
storage     local             {"path":"/uploads", "domain":"https://cdn.example.com"}
storage     aliyun_oss        {"access_key_id":"...", "access_key_secret":"...", "bucket":"...", "endpoint":"...", "domain":""}

notify      default           telegram
notify      telegram          {"bot_token":"...", "chat_id":"..."}
notify      dingtalk          {"webhook":"...", "secret":"..."}
notify      telegram_enabled  1
notify      dingtalk_enabled  1
```

---

## 九、配置加载流程

```
业务代码调用 sms_service.send_code(db, phone, code)
    ↓
BaseService.get_driver(db)
    ↓
setting_logic.get(db, "sms", "default")    → "aliyun"
setting_logic.get(db, "sms", "aliyun")     → {"access_key_id": "...", ...}
    ↓
AliyunSmsDriver(config)
    ↓
driver.send(phone, template_id, {"code": code})
```

Redis 缓存：`settings:all` 已有全量缓存，不会每次查 DB。

---

## 十、错误处理策略

**与 PHP 版的区别：不静默，不 return False。**

| 场景 | 处理方式 |
|------|---------|
| 配置缺失（未配置 default） | 抛 `BizError("未配置短信服务商")` |
| 驱动不支持 | 抛 `BizError("不支持的服务商: xxx")` |
| 凭证错误 | 抛 `BizError("短信发送失败: InvalidAccessKey")` |
| 网络超时 | 抛 `BizError("短信发送失败: 网络超时")`，同时通过 NotifyService 告警 |
| 余额不足 | 抛 `BizError("短信发送失败: 余额不足")`，同时告警 |

**日志记录：**
- 每次调用记录到 `admin_operation_logs`（已有）
- 短信发送记录到 `sms_send_logs` 表（需新建）
- 错误通知通过 `NotifyService.send()` 推送到管理员

---

## 十一、扩展新驱动

### 新增一个 SMS 驱动（以 Twilio 为例）

**1. 创建驱动文件 `app/services/sms/drivers/twilio.py`：**

```python
from app.services.sms.interface import SmsDriver

class TwilioSmsDriver(SmsDriver):
    async def send(self, phone: str, template_id: str, params: dict) -> bool:
        # 实现 Twilio API 调用
        ...

    async def send_raw(self, phone: str, content: str) -> bool:
        # Twilio 支持自定义内容
        ...
```

**2. 注册到 SmsService 的 driver_map：**

```python
class SmsService(BaseService):
    driver_map = {
        ...
        "twilio": TwilioSmsDriver,
    }
```

**3. 前端 settings/sms/index.vue 的 providers 数组加一项：**

```typescript
{
  key: 'twilio', label: 'Twilio', icon: '📱',
  fields: [
    { name: 'account_sid', label: 'Account SID', required: true },
    { name: 'auth_token', label: 'Auth Token', type: 'password', required: true },
    { name: 'from_number', label: '发送号码', required: true },
  ],
}
```

**4. 后台配置页面设置 Twilio 凭证 → settings 表写入 → 选为 default → 生效。**

**不需要改任何业务代码。**

---

## 十二、实现优先级

| 优先级 | 服务 | 驱动 | 说明 |
|--------|------|------|------|
| P0 | **Storage** | local | 基础功能，文件上传必须 |
| P0 | **Notify** | telegram / dingtalk | 系统告警必须 |
| P1 | **SMS** | aliyun | 验证码功能 |
| P1 | **Storage** | aliyun_oss | 生产环境云存储 |
| P2 | **SMS** | qcloud / huawei | 备选短信 |
| P2 | **Storage** | qcloud_cos / s3 | 备选存储 |
| P2 | **Notify** | feishu / wechat_work / email | 备选通知 |
| P3 | **SMS** | aliyun_intl | 国际短信 |
| P3 | **Storage** | qiniu | 小众 |

---

## 十三、影响范围

### 新增文件

| 文件 | 说明 |
|------|------|
| `app/services/base.py` | BaseService 基类（配置加载 + 驱动工厂） |
| `app/services/sms/__init__.py` | SmsService 统一入口 |
| `app/services/sms/drivers/*.py` | 各 SMS 驱动（按需） |
| `app/services/storage/__init__.py` | StorageService 统一入口 |
| `app/services/storage/drivers/*.py` | 各存储驱动（按需） |
| `app/services/notify/__init__.py` | NotifyService 统一入口 |
| `app/services/notify/drivers/*.py` | 各通知驱动（按需） |

### 不改的文件

- settings 表结构 — 不变（category + name 天然支持）
- SettingLogic — 不变（get / set / get_all 已满足）
- 前端配置页面 — 已完成（SettingForm 组件 + 各 category 页面）

### 可能新增的表

| 表 | 说明 |
|----|------|
| `sms_send_logs` | 短信发送记录（手机号、模板、状态、服务商、耗时） |
| `files` | 文件记录（路径、大小、类型、存储驱动、上传者） |
