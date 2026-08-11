"""Trading handlers（WP-02 / WP-03）。

Handler 只解析 event、调用一个 Logic/UoW、返回 completion（实施合同 §8）。
"""

from app.handlers.trading.cognition import CognitionEvent, CognitionHandler
from app.handlers.trading.decision import DecisionEvent, DecisionHandler
from app.handlers.trading.execution import ExecutionEvent, ExecutionHandler

__all__ = [
    "CognitionEvent",
    "CognitionHandler",
    "DecisionEvent",
    "DecisionHandler",
    "ExecutionEvent",
    "ExecutionHandler",
]
