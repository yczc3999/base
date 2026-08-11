"""Trading handlers（WP-02 / WP-03）。

Handler 只解析 event、调用一个 Logic/UoW、返回 completion（实施合同 §8）。
"""

from app.handlers.trading.cognition import CognitionEvent, CognitionHandler
from app.handlers.trading.decision import DecisionEvent, DecisionHandler
from app.handlers.trading.execution import ExecutionEvent, ExecutionHandler
from app.handlers.trading.settlement import (
    HandlerResult as SettlementHandlerResult,
)
from app.handlers.trading.settlement import SettlementEvent, SettlementHandler
from app.handlers.trading.evaluation import (
    HandlerResult as EvaluationHandlerResult,
)
from app.handlers.trading.evaluation import EvaluationEvent, EvaluationHandler

__all__ = [
    "CognitionEvent",
    "CognitionHandler",
    "DecisionEvent",
    "DecisionHandler",
    "ExecutionEvent",
    "ExecutionHandler",
    # WP-04
    "SettlementEvent",
    "SettlementHandler",
    "SettlementHandlerResult",
    "EvaluationEvent",
    "EvaluationHandler",
    "EvaluationHandlerResult",
]
