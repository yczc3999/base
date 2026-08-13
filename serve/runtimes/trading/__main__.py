"""Trading 常驻运行时进程入口（WP-07C）。

用法：
    python -m runtimes.trading --dry-run     # 打印注册清单，不联网、不启动
    python -m runtimes.trading               # 装配并常驻运行（Ctrl+C / SIGTERM 优雅关闭）

进程隔离：每个 runtime 用各自 DB pool profile；outbox 三件套 + pipeline 常驻，
cognition/execution/evaluation/reconciliation/replay 已注册：缺网关 idle，
``PM_V2_PIPELINE_AI_ENABLED`` 且无 gateway 则 fail closed。
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from app.config import settings
from app.services.database import engines
from app.services.redis_cache import CacheRedisClient
from app.services.redis_control import ControlRedisClient
from app.services.artifact_store.factory import build_artifact_store

from runtimes.trading.assembly import build_dispatch, default_specs
from runtimes.trading.supervisor import RuntimeSupervisor, SupervisorContext


def _build_context() -> SupervisorContext:
    """装配 supervisor 依赖容器；import 本模块不联网，网络只发生在 run()。"""
    control = ControlRedisClient(settings.control_redis_endpoint)
    cache = CacheRedisClient(settings.cache_redis_endpoint)
    artifacts = build_artifact_store(settings)
    ctx = SupervisorContext(
        session_factory_for=engines.session_factory,
        control_redis=control,
        cache_redis=cache,
        artifacts=artifacts,
        gateway=None,
    )
    # dispatch 工厂：5 个域 handler 由 build_dispatch 注入（需 provider/网关时再补）。
    ctx.config["build_dispatch"] = lambda c: build_dispatch(c)
    return ctx


async def _amain(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m runtimes.trading")
    parser.add_argument("--dry-run", action="store_true", help="只打印注册清单后退出")
    args = parser.parse_args(argv)

    ctx = _build_context()
    supervisor = RuntimeSupervisor(ctx)
    for spec in default_specs():
        supervisor.register(spec)

    if args.dry_run:
        for row in supervisor.registry_snapshot():
            print(f"{row['name']:<24} pool={row['pool_profile']}")
        return 0

    failures = await supervisor.run()
    await supervisor.shutdown()
    await engines.dispose()
    return 1 if failures else 0


def main() -> None:
    sys.exit(asyncio.run(_amain(sys.argv[1:])))


if __name__ == "__main__":
    main()
