"""
通用工具函数

提供与业务无关的通用工具方法，包括：
- IP 地址解析（支持反向代理 + CDN）
- 树形结构转换（菜单、部门、分类等）
- 唯一编码生成（订单号、业务编号等）
- 时间戳校验
- 字符串检测 / 截断
"""

import re
import time
import secrets
from datetime import datetime
from fastapi import Request


def get_client_ip(request: Request) -> str:
    """
    获取客户端真实 IP 地址

    支持多层 CDN / 反向代理环境，按以下优先级依次尝试：

    1. CDN 专有头（不可伪造，由 CDN 边缘节点写入）
       - CF-Connecting-IP（Cloudflare）
       - X-Vercel-Forwarded-For（Vercel）
       - Ali-CDN-Real-IP（阿里云 CDN）
       - X-Tencent-Real-IP（腾讯云 CDN）
       - True-Client-IP（Akamai / Cloudflare Enterprise）
    2. X-Real-IP（Nginx 常用配置）
    3. X-Forwarded-For（多级代理链，取第一个合法公网 IP）
    4. request.client.host（直连）

    安全说明：
    - CDN 专有头由 CDN 边缘节点覆写，客户端无法伪造
    - X-Forwarded-For 可被客户端伪造，本函数会校验 IP 格式
      并跳过私有 IP（10.x / 172.16-31.x / 192.168.x / 127.x）
    - 生产环境建议配置 Nginx：
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;

    :param request: FastAPI Request 对象
    :return: 客户端 IP 字符串，获取失败返回 "0.0.0.0"
    """
    # ── 1. CDN 专有头（优先级最高，不可伪造）──
    cdn_headers = [
        "cf-connecting-ip",        # Cloudflare
        "x-vercel-forwarded-for",  # Vercel
        "ali-cdn-real-ip",         # 阿里云 CDN
        "x-tencent-real-ip",       # 腾讯云 CDN
        "true-client-ip",          # Akamai / Cloudflare Enterprise
    ]
    for header in cdn_headers:
        ip = request.headers.get(header)
        if ip:
            ip = ip.split(",")[0].strip()
            if _is_valid_public_ip(ip):
                return ip

    # ── 2. X-Real-IP（Nginx 写入）──
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        real_ip = real_ip.strip()
        if _is_valid_public_ip(real_ip):
            return real_ip

    # ── 3. X-Forwarded-For（多级代理链）──
    #    格式：client, proxy1, proxy2
    #    从左到右找第一个合法公网 IP
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        for ip in forwarded.split(","):
            ip = ip.strip()
            if _is_valid_public_ip(ip):
                return ip

    # ── 4. 直连 ──
    if request.client:
        return request.client.host

    return "0.0.0.0"


def _is_valid_public_ip(ip: str) -> bool:
    """
    校验是否为合法的公网 IP 地址

    排除：私有 IP、回环地址、格式非法的字符串
    """
    if not ip:
        return False
    try:
        import ipaddress
        addr = ipaddress.ip_address(ip)
        # 排除私有、回环、链路本地
        return addr.is_global
    except (ValueError, TypeError):
        return False


def to_tree(
    data: list[dict],
    parent_id: int = 0,
    parent_key: str = "parent_id",
    children_key: str = "children",
    id_key: str = "id",
) -> list[dict]:
    """
    将扁平列表转换为树形结构

    适用于菜单、部门、分类等有父子关系的数据。
    使用 O(n) 分组 + 递归构建，不修改原始数据。

    :param data:         扁平数据列表，每项必须包含 id_key 和 parent_key
    :param parent_id:    根节点的 parent_id 值（默认 0）
    :param parent_key:   父节点 ID 字段名（默认 "parent_id"）
    :param children_key: 子节点列表字段名（默认 "children"）
    :param id_key:       主键字段名（默认 "id"）
    :return:             树形结构列表

    示例：
        data = [
            {"id": 1, "name": "系统管理", "parent_id": 0},
            {"id": 2, "name": "用户管理", "parent_id": 1},
            {"id": 3, "name": "角色管理", "parent_id": 1},
            {"id": 4, "name": "日志管理", "parent_id": 0},
        ]
        tree = to_tree(data)
        # → [
        #     {"id": 1, "name": "系统管理", "parent_id": 0, "children": [
        #         {"id": 2, "name": "用户管理", "parent_id": 1},
        #         {"id": 3, "name": "角色管理", "parent_id": 1},
        #     ]},
        #     {"id": 4, "name": "日志管理", "parent_id": 0},
        # ]
    """
    # 按 parent_id 分组
    children_map: dict[int, list] = {}
    for item in data:
        pid = item.get(parent_key, 0)
        if pid not in children_map:
            children_map[pid] = []
        children_map[pid].append(item)

    def _build(pid: int) -> list[dict]:
        nodes = children_map.get(pid, [])
        for node in nodes:
            kids = _build(node[id_key])
            if kids:
                node[children_key] = kids
        return nodes

    return _build(parent_id)


def has_chinese(text: str) -> bool:
    """
    判断字符串是否包含中文字符

    检测范围：CJK 统一汉字（U+4E00 ~ U+9FFF）

    :param text: 待检测字符串
    :return: 包含中文返回 True

    示例：
        has_chinese("hello")    # → False
        has_chinese("你好")     # → True
        has_chinese("hi你好")   # → True
    """
    return bool(re.search(r'[\u4e00-\u9fff]', text))


def create_code(prefix: str = "", length: int = 16) -> str:
    """
    生成唯一编码

    格式：{prefix}{YYYYMMDD}{随机字符}
    适用于订单号、编号等业务场景。

    :param prefix: 前缀（如 "ORD"、"INV"）
    :param length: 随机部分长度（默认 16）
    :return: 唯一编码字符串

    示例：
        create_code("ORD")      # → "ORD20260401a3f8b7c2d9e1f0g4"
        create_code()           # → "20260401x7k9m2p4q8r1s5t3"
        create_code("", 8)      # → "20260401a3f8b7c2"
    """
    date_part = datetime.now().strftime("%Y%m%d")
    random_part = secrets.token_hex(length // 2 + 1)[:length]
    return f"{prefix}{date_part}{random_part}"


def is_valid_timestamp(value) -> bool:
    """
    校验是否为合法时间戳（秒级或毫秒级）

    :param value: 待校验的值
    :return: 合法返回 True

    示例：
        is_valid_timestamp(1711900800)      # → True（秒级）
        is_valid_timestamp(1711900800000)   # → True（毫秒级）
        is_valid_timestamp("1711900800")    # → True（字符串也行）
        is_valid_timestamp("abc")           # → False
        is_valid_timestamp(-1)              # → False
    """
    try:
        ts = int(value)
        # 毫秒级转秒级
        if ts > 1e12:
            ts = ts // 1000
        # 合理范围：2000-01-01 ~ 2100-01-01
        return 946684800 <= ts <= 4102444800
    except (ValueError, TypeError):
        return False


def truncate(text: str, max_length: int = 200, suffix: str = "...") -> str:
    """
    截断长字符串

    :param text:       原始字符串
    :param max_length: 最大长度
    :param suffix:     截断后缀
    :return: 截断后的字符串

    示例：
        truncate("很长的错误信息...", 10)  # → "很长的错误信息..."
    """
    if not text or len(text) <= max_length:
        return text
    return text[: max_length - len(suffix)] + suffix
