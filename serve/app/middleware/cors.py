"""
CORS 跨域中间件配置

注意：
- allow_origins=["*"] 时不能同时 allow_credentials=True（浏览器拒绝）
- 生产环境应在 .env 中配置具体域名：CORS_ORIGINS=https://admin.example.com,https://www.example.com
"""

from fastapi.middleware.cors import CORSMiddleware
from app.config import settings

# 从 .env 读取允许的源，逗号分隔，默认 "*"
_origins_str = getattr(settings, "CORS_ORIGINS", "*")
_origins = [o.strip() for o in _origins_str.split(",")] if _origins_str != "*" else ["*"]

CORS_CONFIG = {
    "allow_origins": _origins,
    "allow_credentials": _origins != ["*"],  # 指定域名时允许携带 Cookie，通配符时不允许
    "allow_methods": ["*"],
    "allow_headers": ["*"],
}
