"""
Redis key 唯一编码 — Control/Cache 共享的 key 构造原语（WP-00b-r2）。

identity 协议（类型明确、可运维、无歧义）：

    完整 key = {validated_namespace}:~:{encoded_segment_1}:{encoded_segment_2}…

- namespace 保持可读层级，如 `pm:v2:prod:cache`，**不编码**；只允许非空
  `[A-Za-z0-9._-]+` 层以 `:` 分隔，`~` 保留、非法。
- `:~:` 是 namespace 与动态 segment 之间的保留边界标记。namespace 不含 `~`，
  动态段中 `~` 必被编码为 `%7E`，因此 `~` 只在边界出现一次。
- 动态 segment 用可逆百分号编码：安全字符 [A-Za-z0-9._-] 保留，其余字符按
  UTF-8 字节逐一编码为 %XX；`:` → `%3A`、`%` → `%25`、空格 → `%20`、`~` → `%7E`。
  编码段不含裸 `:` 或 `~`，段间用 `:` 分隔。

无碰撞保证（构造性）：
- namespace 层不含 `~` → 首个 `~` 唯一标记边界；
- 段不含裸 `:` → `:` 只做段间分隔；
- 段不含裸 `~` → 边界 `~` 不会与段内容混淆；
- `encode_key_segment` 只接受 `str`，非字符串抛 `TypeError`（禁止隐式 str()），
  杜绝整数 1 与字符串 "1" 碰撞。

两个客户端都必须调用 build_redis_key，禁止复制编码逻辑。
"""

from __future__ import annotations

import re

_SAFE_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789._-"
)

# namespace 层级：每层 [A-Za-z0-9._-]+，层间 : 分隔；~ 保留、非法
_NAMESPACE_LAYER_RE = re.compile(r"^[A-Za-z0-9._-]+$")

# 保留边界标记：namespace 与动态 segment 之间的唯一分隔
BOUNDARY = ":~:"


def encode_key_segment(value: str) -> str:
    """
    把单个动态 segment 编码为不含裸 `:` / `~` 的稳定字符串。

    - 只接受 str；任何非字符串立即抛 TypeError（禁止隐式 str()）；
    - 相同输入 → 相同输出；
    - 支持 Unicode、`:`、`%`、`~`、空格、斜杠等任意字符；
    - 编码一一映射：decode(encode(s)) == s 必然成立。
    """
    if not isinstance(value, str):
        raise TypeError(
            f"key segment must be str, got {type(value).__name__}; "
            "callers must str() explicitly to keep identity type-bound"
        )
    out: list[str] = []
    for ch in value:
        if ch in _SAFE_CHARS:
            out.append(ch)
        else:
            for byte in ch.encode("utf-8"):
                out.append(f"%{byte:02X}")
    return "".join(out)


def validate_namespace(namespace: str) -> str:
    """
    校验并返回可读 namespace。

    - 必须为 str（非字符串抛 TypeError）；
    - 非空；`~` 保留非法；
    - 每层为 [A-Za-z0-9._-]+，层间以 `:` 分隔，不允许空层。
    """
    if not isinstance(namespace, str):
        raise TypeError(
            f"namespace must be str, got {type(namespace).__name__}"
        )
    if not namespace:
        raise ValueError("namespace must be non-empty")
    if "~" in namespace:
        raise ValueError(
            f"namespace contains reserved boundary char '~': {namespace!r}"
        )
    layers = namespace.split(":")
    if any(not _NAMESPACE_LAYER_RE.match(layer) for layer in layers):
        raise ValueError(
            "namespace layers must match [A-Za-z0-9._-]+ (colon-separated, "
            f"non-empty), got {namespace!r}"
        )
    return namespace


def build_redis_key(namespace: str, *segments: str) -> str:
    """
    构造完整 Redis key。

    namespace 保持可读层级（经 validate_namespace），动态 segment 各经
    encode_key_segment 编码，以 BOUNDARY(:~:) + ':' 拼接。

    不同 namespace/segment 分割不碰撞；相同输入稳定。
    """
    ns = validate_namespace(namespace)
    # 精确格式 {namespace}:~:{encoded...}；BOUNDARY 含 :~:，故以 ":" 拼接段
    if not segments:
        return f"{ns}:~:"
    encoded = [encode_key_segment(seg) for seg in segments]
    return f"{ns}:~:" + ":".join(encoded)


def decode_key_segment(encoded: str) -> str:
    """
    逆编码（还原 encode_key_segment 的结果）。

    仅用于测试/审计还原，不参与 key 构造热路径。
    """
    out: list[bytes] = []
    i = 0
    n = len(encoded)
    while i < n:
        ch = encoded[i]
        if ch == "%":
            if i + 2 >= n:
                raise ValueError(f"truncated escape in {encoded!r}")
            out.append(bytes([int(encoded[i + 1:i + 3], 16)]))
            i += 3
        else:
            out.append(ch.encode("utf-8"))
            i += 1
    return b"".join(out).decode("utf-8")
