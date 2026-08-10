"""Trading ORM 常量（WP-01A-02）。

``TRADING_SCHEMA`` 是 V2 物理 schema 的唯一常量来源，其他模块（models/env/revision）
一律从这里导入，不得内联字符串。
"""

TRADING_SCHEMA = "trading"
