"""V2 orchestrator 包（WP-01C Checkpoint C）。

跨阶段顺序由 ``trading_state_machine`` 唯一定义；各 Logic 只实现 Gate 内部算法。
"""

from app.orchestrator.trading_state_machine import (
    EpisodeInput,
    EpisodeKeyMaterial,
    IllegalTransitionError,
    TradingStateMachine,
    episode_key,
)

__all__ = [
    "EpisodeInput",
    "EpisodeKeyMaterial",
    "IllegalTransitionError",
    "TradingStateMachine",
    "episode_key",
]
