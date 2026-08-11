"""Trading repositories 包（WP-01C / WP-02 / WP-03）。

Repository 只拥有 SQL / 显式列投影 / CAS；**绝不 commit、不调用网络、不做业务判断**。
"""

from app.repositories.trading.cohort import CohortRepository
from app.repositories.trading.decision import DecisionRepository
from app.repositories.trading.execution import ExecutionRepository
from app.repositories.trading.forecast import ForecastRepository
from app.repositories.trading.ledger import LedgerRepository
from app.repositories.trading.market import MarketRepository
from app.repositories.trading.market_stream import MarketStreamRepository
from app.repositories.trading.semantics import SemanticsRepository
from app.repositories.trading.workflow import WorkflowRepository
from app.repositories.trading.settlement import SettlementRepository
from app.repositories.trading.evaluation import EvaluationRepository
from app.repositories.trading.audit import AuditRepository
from app.repositories.trading.projection import ProjectionRepository
from app.repositories.trading.vault import VaultRepository

__all__ = [
    "CohortRepository",
    "DecisionRepository",
    "ExecutionRepository",
    "ForecastRepository",
    "LedgerRepository",
    "MarketRepository",
    "MarketStreamRepository",
    "SemanticsRepository",
    "WorkflowRepository",
    # WP-04
    "SettlementRepository",
    "EvaluationRepository",
    "AuditRepository",
    "ProjectionRepository",
    # WP-05
    "VaultRepository",
]
