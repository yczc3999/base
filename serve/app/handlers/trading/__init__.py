"""Trading handlers（WP-02 Checkpoint C）。

Handler 只解析 event、调用一个 Logic/UoW、返回 completion（实施合同 §8）。
"""

from app.handlers.trading.cognition import CognitionEvent, CognitionHandler, HandlerResult

__all__ = [
    "CognitionEvent",
    "CognitionHandler",
    "HandlerResult",
]
