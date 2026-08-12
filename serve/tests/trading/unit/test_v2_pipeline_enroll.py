"""WP-07C：pipeline 登记（frame → cohort membership）单元测试（无 DB）。

验证根治后的 _enroll：用最近 COMPLETE frame 的显式归属（AppliedMarket）
构造 HydratedUniverseFrameInput，调 ScreeningLogic.enroll_frame 写 membership。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace

from runtimes.trading.pipeline import PipelineDriver, PipelinePolicy


def _run(coro):
    return asyncio.run(coro)


class _G0Ok:
    ok = True


class _FakeScreening:
    def __init__(self):
        self.g0_calls = 0
        self.enroll_calls = []

    async def run_g0(self, uow, cohort_id):
        self.g0_calls += 1
        return _G0Ok()

    async def enroll_frame(self, uow, *, cohort_id, frame, observed_at, ingested_at, g0):
        self.enroll_calls.append(
            {
                "cohort_id": cohort_id,
                "frame_id": frame.frame_id,
                "market_ids": [m.market_id for m in frame.markets],
                "metadata": [m.metadata for m in frame.markets],
            }
        )


@dataclass
class _Frame:
    frame_id: int = 1
    status: str = "COMPLETE"
    content_hash: str = "c" * 64
    artifact_id: int = 5
    artifact_ref: str = "d" * 64
    markets: tuple = ()


def _mk_market(market_id, metadata):
    return SimpleNamespace(market_id=market_id, metadata=metadata)


class _FakeSession:
    async def commit(self):
        pass

    async def rollback(self):
        pass

    async def close(self):
        pass


def _driver(screening, cohorts):
    d = PipelineDriver(
        sessions_factory=lambda p: _FakeSession,  # 可调用构造 fake session
        universe_ingestor=None,
        policy=PipelinePolicy(screen_enabled=True),
    )
    d._screening = screening
    d._open_cohorts = lambda session: asyncio.sleep(0, result=cohorts)
    return d


def test_enroll_no_frame_returns_no_complete_frame():
    screening = _FakeScreening()
    d = _driver(screening, [1])
    d._last_frame = None
    result = _run(d._enroll())
    assert result["reason"] == "no_complete_frame"
    assert screening.enroll_calls == []


def test_enroll_constructs_hydrated_frame_and_calls_enroll():
    screening = _FakeScreening()
    d = _driver(screening, [1])
    d._last_frame = _Frame(
        markets=(
            _mk_market(10, {"q": "m1"}),
            _mk_market(11, {"q": "m2"}),
        ),
    )
    result = _run(d._enroll())
    assert result["ok"] is True
    assert result["markets"] == 2
    assert len(screening.enroll_calls) == 1
    call = screening.enroll_calls[0]
    assert call["cohort_id"] == 1
    assert call["frame_id"] == 1
    assert call["market_ids"] == [10, 11]
    assert call["metadata"] == [{"q": "m1"}, {"q": "m2"}]


def test_enroll_skips_failed_frame():
    screening = _FakeScreening()
    d = _driver(screening, [1])
    d._last_frame = _Frame(status="FAILED", markets=())
    result = _run(d._enroll())
    assert result["reason"] == "frame_not_complete"
    assert screening.enroll_calls == []


def test_enroll_runs_for_each_open_cohort():
    screening = _FakeScreening()
    d = _driver(screening, [1, 2, 3])
    d._last_frame = _Frame(markets=(_mk_market(10, {"q": "m"}),))
    result = _run(d._enroll())
    assert result["cohorts"] == 3
    assert len(screening.enroll_calls) == 3
    assert {c["cohort_id"] for c in screening.enroll_calls} == {1, 2, 3}


def test_sense_saves_complete_frame():
    class _Ingestor:
        async def run_once(self):
            return _Frame(status="COMPLETE", markets=(_mk_market(1, {"q": "x"}),))

    d = PipelineDriver(
        sessions_factory=lambda p: None,
        universe_ingestor=_Ingestor(),
        policy=PipelinePolicy(screen_enabled=False),
    )
    _run(d._sense())
    assert d._last_frame is not None
    assert d._last_frame.markets[0].market_id == 1
