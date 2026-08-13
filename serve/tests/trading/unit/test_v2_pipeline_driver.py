"""WP-07C：PipelineDriver 单元测试（无 DB、无网络）。

验证驱动循环、Stage 0/1 编排、fail-closed 与 AI 门控。DB 访问用 fake 替身。
"""

from __future__ import annotations

import asyncio

from runtimes.trading.pipeline import PipelineDriver, PipelinePolicy


def _run(coro):
    return asyncio.run(coro)


class _FakeIngestor:
    def __init__(self, frame_id=1, status="COMPLETE", fail=False):
        self._frame_id = frame_id
        self._status = status
        self._fail = fail
        self.calls = 0

    async def run_once(self):
        self.calls += 1
        if self._fail:
            raise RuntimeError("gamma_down")
        from types import SimpleNamespace
        return SimpleNamespace(frame_id=self._frame_id, status=self._status)


def test_sense_runs_ingestor():
    d = PipelineDriver(sessions_factory=lambda p: None,
                       universe_ingestor=_FakeIngestor(frame_id=7),
                       policy=PipelinePolicy(screen_enabled=False))
    s = _run(d.run_once())
    assert s["sense"]["ok"] is True
    assert s["sense"]["frame_id"] == 7
    assert s["sense"]["tags"]["reason"] == "sync_not_configured"


def test_sense_syncs_tag_catalog_before_universe_frame():
    class _WithTags(_FakeIngestor):
        def __init__(self):
            super().__init__(frame_id=3)
            self.tag_calls = 0

        async def sync_tag_catalog(self):
            self.tag_calls += 1
            return {"stage": "tags", "ok": True, "upserted": 2, "pages": 1}

    ingestor = _WithTags()
    d = PipelineDriver(sessions_factory=lambda p: None, universe_ingestor=ingestor,
                       policy=PipelinePolicy(screen_enabled=False))
    s = _run(d.run_once())
    assert ingestor.tag_calls == 1
    assert ingestor.calls == 1
    assert s["sense"]["tags"]["upserted"] == 2


def test_tag_catalog_failure_does_not_block_universe_frame():
    class _BrokenTags(_FakeIngestor):
        async def sync_tag_catalog(self):
            raise RuntimeError("tags_down")

    d = PipelineDriver(sessions_factory=lambda p: None,
                       universe_ingestor=_BrokenTags(frame_id=9),
                       policy=PipelinePolicy(screen_enabled=False))
    s = _run(d.run_once())
    assert s["sense"]["ok"] is True
    assert s["sense"]["frame_id"] == 9
    assert s["sense"]["tags"]["ok"] is False
    assert s["sense"]["tags"]["reason"] == "RuntimeError"


def test_sense_without_ingestor_fail_closed():
    d = PipelineDriver(sessions_factory=lambda p: None, universe_ingestor=None,
                       policy=PipelinePolicy(screen_enabled=False))
    s = _run(d.run_once())
    assert s["sense"]["ok"] is False
    assert s["sense"]["reason"] == "ingestor_not_configured"


def test_stage_failure_does_not_break_loop():
    """sense 抛异常 → fail closed，但 driver 可继续下一轮。"""
    ingestor = _FakeIngestor(fail=True)
    d = PipelineDriver(sessions_factory=lambda p: None, universe_ingestor=ingestor,
                       policy=PipelinePolicy(screen_enabled=False))
    s = _run(d.run_once())
    assert s["sense"]["ok"] is False
    assert s["sense"]["reason"] == "RuntimeError"
    # 第二轮仍可跑（驱动器未崩溃）
    s2 = _run(d.run_once())
    assert "sense" in s2


def test_ai_stages_gated_by_default():
    d = PipelineDriver(sessions_factory=lambda p: None, universe_ingestor=None,
                       policy=PipelinePolicy(ai_enabled=False))
    s = _run(d.run_once())
    assert "opportunities" not in s
    assert "episodes" not in s
    assert "decisions" not in s


def test_ai_stages_when_enabled_return_gated():
    d = PipelineDriver(sessions_factory=lambda p: None, universe_ingestor=None,
                       policy=PipelinePolicy(ai_enabled=True, screen_enabled=False,
                                             sense_enabled=False))
    s = _run(d.run_once())
    assert s["opportunities"]["reason"] == "ai_gated"
    assert s["episodes"]["reason"] == "ai_gated"
    assert s["decisions"]["reason"] == "ai_gated"


def test_run_loop_stops_on_event():
    ingestor = _FakeIngestor()
    d = PipelineDriver(sessions_factory=lambda p: None, universe_ingestor=ingestor,
                       policy=PipelinePolicy(interval_s=0.01, screen_enabled=False))

    async def main():
        stop = asyncio.Event()
        task = asyncio.create_task(d.run(stop))
        await asyncio.sleep(0.05)
        stop.set()
        await asyncio.wait_for(task, timeout=2)
        return ingestor.calls

    calls = _run(main())
    assert calls >= 2, f"loop 应多轮调用 sense，实际 {calls}"
