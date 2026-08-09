"""
V2 基础运行时（WP-00d2）— 资源所有权、健康快照、安全关闭。

- `build_runtime_resources(cfg, *, db_engines=None, control=None, cache=None, artifact=None)`：
  依赖缺省时按 cfg 构造真实 ControlRedis/CacheRedis/ArtifactStore；DB probe 使用 engines 的
  api profile。import 本模块零网络；网络只发生在 lifespan 或显式 health 调用。
- `RuntimeResources.health_snapshot()`：并发、每项受 `RUNTIME_HEALTH_TIMEOUT_S` 约束；固定结构、
  仅安全状态。DB/Control/Artifact 失败或超时 → unready；Cache 失败 → ready+degraded。
- `RuntimeResources.close()`：先禁止 ready，再逆序尽力关闭（Artifact sync → Cache → Control →
  DB dispose），每项至多一次、重复关闭幂等。
- 禁止输出异常 message/traceback/DSN/host/port/namespace/文件路径/bucket/endpoint/credential/pool
  对象或 Artifact detail。
"""

from __future__ import annotations

import asyncio
import datetime
from typing import Awaitable, Callable

from sqlalchemy import text

from app.config import Settings
from app.services.artifact_store import ArtifactStore
from app.services.artifact_store.factory import build_artifact_store
from app.services.redis_cache import CacheRedisClient
from app.services.redis_control import ControlRedisClient


def _rfc3339_utc() -> str:
    now = datetime.datetime.now(datetime.timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S") + f".{now.microsecond // 1000:03d}Z"


def safe_unready_snapshot(driver: str | None = None) -> dict:
    """唯一 safe/unavailable 快照 builder（runtime 内部、health 编排异常、Controller 均复用）。
    字段完全一致：database/control/artifact=unready、cache=degraded、degraded=["cache_redis"]、
    checked_at=UTC RFC3339。driver 为冻结配置的 ARTIFACT_DRIVER（local|s3）；缺省 local。"""
    d = driver if driver in ("local", "s3") else "local"
    return {
        "status": "unready",
        "components": {
            "database": {"state": "unready"},
            "control_redis": {"state": "unready"},
            "cache_redis": {"state": "degraded"},
            "artifact_store": {"state": "unready", "driver": d},
        },
        "degraded": ["cache_redis"],
        "checked_at": _rfc3339_utc(),
    }


class RuntimeResources:
    """持有 V2 基础运行时资源；并发 health 只读，不修改资源身份、不重复构造。"""

    def __init__(
        self,
        cfg: Settings,
        *,
        db_probe: Callable[[], Awaitable[None]],
        control: ControlRedisClient,
        cache: CacheRedisClient,
        artifact: ArtifactStore,
        db_engines=None,
    ) -> None:
        self._cfg = cfg
        self._db_probe = db_probe
        self._control = control
        self._cache = cache
        self._artifact = artifact
        self._db_engines = db_engines
        self._started = False
        self._closed = False
        self._last_snapshot: dict | None = None
        self._last_failed_components: list[str] = []

    # ---- 生命周期 ----

    def mark_started(self) -> None:
        """startup 完成（首次 health snapshot 写入后）才可报告 ready。"""
        self._started = True

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def last_snapshot(self) -> dict | None:
        """最近一次安全快照（Controller 只读复用；None=尚未启动）。"""
        return self._last_snapshot

    async def close(self) -> list[str]:
        """逆序尽力关闭；开始即把最近快照替换为固定 unready；每项至多一次；重复幂等。
        返回固定失败 component 集合（未关闭/关闭失败的项名），使 main 可记录安全 reason code，
        不得静默丢掉关闭失败。"""
        if self._closed:
            return self._last_failed_components
        self._closed = True
        self._last_snapshot = safe_unready_snapshot(self._cfg.ARTIFACT_DRIVER)
        failed: list[str] = []
        try:
            self._artifact.aclose()  # 同步 close
        except Exception:  # noqa: BLE001 - 继续尝试后续项
            failed.append("artifact_store")
        for name, client in (("cache_redis", self._cache), ("control_redis", self._control)):
            try:
                await client.aclose()
            except Exception:  # noqa: BLE001
                failed.append(name)
        if self._db_engines is not None:
            try:
                await self._db_engines.dispose()
            except Exception:  # noqa: BLE001
                failed.append("database")
        self._last_failed_components = failed
        return failed

    # ---- 健康 ----

    async def health_snapshot(self) -> dict:
        """closed/not-started 不再访问已关闭依赖，直接返回固定 unready；started 才并发探测。
        artifact driver 始终取冻结配置的 ARTIFACT_DRIVER（不输出 unknown）；任何 probe 形状
        异常映射为固定 unready，不泄对象 repr/message。"""
        if self._closed or not self._started:
            snap = safe_unready_snapshot(self._cfg.ARTIFACT_DRIVER)
            self._last_snapshot = snap
            return snap
        timeout = self._cfg.RUNTIME_HEALTH_TIMEOUT_S

        async def _probe_db() -> None:
            result = await asyncio.wait_for(self._db_probe(), timeout)
            if result is not None:
                raise RuntimeError("database")

        async def _probe_control() -> dict:
            r = await asyncio.wait_for(self._control.health(), timeout)
            if not isinstance(r, dict) or r.get("ok") is not True:
                raise RuntimeError("control")
            return r

        async def _probe_cache() -> dict:
            r = await asyncio.wait_for(self._cache.health(), timeout)
            if not isinstance(r, dict) or r.get("ok") is not True:
                raise RuntimeError("cache")
            return r

        async def _probe_artifact():
            h = await asyncio.wait_for(
                asyncio.to_thread(self._artifact.health), timeout
            )
            if getattr(h, "ok", None) is not True:
                raise RuntimeError("artifact")
            return h

        results = await asyncio.gather(
            _probe_db(), _probe_control(), _probe_cache(), _probe_artifact(),
            return_exceptions=True,
        )
        # health 与 close 可并发：close 在 probe 在途时已将 runtime 终止。
        # 此时必须丢弃 probe 结果，禁止把 closed 状态重写为 ready。
        if self._closed:
            snap = safe_unready_snapshot(self._cfg.ARTIFACT_DRIVER)
            self._last_snapshot = snap
            return snap
        # probe 成功返回非异常值（DB→None / control·cache→dict / artifact→ArtifactHealth）；
        # 任何异常/超时/失败/malformed 形状均映射为固定 unready，不泄对象 repr/message
        db_ok = not isinstance(results[0], BaseException)
        ctl_ok = not isinstance(results[1], BaseException)
        cache_ok = not isinstance(results[2], BaseException)
        art_ok = not isinstance(results[3], BaseException)
        # driver 恒为冻结配置值，不读取 probe 结果（artifact 失败也不输出 unknown）
        art_driver = self._cfg.ARTIFACT_DRIVER

        required_ok = db_ok and ctl_ok and art_ok
        status = "ready" if required_ok else "unready"
        snapshot = {
            "status": status,
            "components": {
                "database": {"state": "ready" if db_ok else "unready"},
                "control_redis": {"state": "ready" if ctl_ok else "unready"},
                "cache_redis": {"state": "ready" if cache_ok else "degraded"},
                "artifact_store": {
                    "state": "ready" if art_ok else "unready",
                    "driver": art_driver,
                },
            },
            "degraded": ["cache_redis"] if not cache_ok else [],
            "checked_at": _rfc3339_utc(),
        }
        self._last_snapshot = snapshot
        return snapshot


async def build_runtime_resources(
    cfg: Settings,
    *,
    db_engines=None,
    control: ControlRedisClient | None = None,
    cache: CacheRedisClient | None = None,
    artifact: ArtifactStore | None = None,
) -> RuntimeResources:
    """可等待的异常安全 builder。依赖缺省时按 cfg 建真实 client / DB probe；**任何构造步骤
    失败时逆序关闭本次自己创建**的 Artifact/Cache/Control 资源后重新抛出，不关闭调用方注入
    对象（不会遗留连接池）。"""
    created: list[tuple[str, object]] = []

    async def _close_created() -> None:
        for kind, obj in reversed(created):
            try:
                if kind == "artifact":
                    obj.aclose()  # type: ignore[attr-defined]  # 同步
                else:
                    await obj.aclose()  # type: ignore[attr-defined, misc]
            except Exception:  # noqa: BLE001 - 清理尽力而为
                pass

    try:
        if db_engines is None:
            from app.services.database import engines as module_engines

            db_engines = module_engines
        if control is None:
            control = ControlRedisClient(cfg.control_redis_endpoint)
            created.append(("control", control))
        if cache is None:
            cache = CacheRedisClient(cfg.cache_redis_endpoint)
            created.append(("cache", cache))
        if artifact is None:
            artifact = build_artifact_store(cfg)
            created.append(("artifact", artifact))

        async def _db_probe() -> None:
            async with db_engines.engine("api").connect() as conn:
                await conn.execute(text("SELECT 1"))

        return RuntimeResources(
            cfg,
            db_probe=_db_probe,
            control=control,
            cache=cache,
            artifact=artifact,
            db_engines=db_engines,
        )
    except BaseException:
        await _close_created()
        raise
