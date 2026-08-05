"""
P1-4/P1-5 测试 — system_monitor 指标采集 + migration status

覆盖:
  1. _memory_metrics 读 /proc/meminfo (Linux)
  2. _disk_metrics 读项目根分区
  3. system_monitor.run() 写入 system:metrics 含 memory/disk/redis/queues
  4. get_status_list 返回迁移状态列表
"""

import json
import pytest

from app.tasks.system_monitor import _memory_metrics, _disk_metrics, SystemMonitorTask


# ==================== 指标采集 ====================

def test_memory_metrics():
    data = _memory_metrics()
    # Linux 下应读到内存
    if data:
        assert data["mem_total"] > 0
        assert "mem_used_percent" in data
        assert 0 <= data["mem_used_percent"] <= 100


def test_disk_metrics():
    data = _disk_metrics()
    if data:
        assert data["disk_total"] > 0
        assert "disk_used_percent" in data


@pytest.mark.asyncio
async def test_system_monitor_writes_full_metrics(mock_redis):
    task = SystemMonitorTask()
    await task.run()

    raw = await mock_redis.get("system:metrics")
    assert raw is not None
    metrics = json.loads(raw)
    # 核心指标
    assert "load_1" in metrics
    assert "cpu_count" in metrics
    assert "ts" in metrics
    # 扩展指标
    assert "memory" in metrics
    assert "disk" in metrics
    assert "redis" in metrics
    assert "queues" in metrics
    assert metrics["queues"]["default"] == 0  # 空队列


# ==================== migration status ====================

@pytest.mark.asyncio
async def test_get_status_list():
    from app.migrate import get_status_list
    data = await get_status_list(url="sqlite+aiosqlite:///:memory:")
    assert isinstance(data, list)
    assert data, "应扫描到 migration 文件"
    for item in data:
        assert "version" in item
        assert "applied" in item
        assert "applied_at" in item
    # 021_dicts.sql 应在列表里 (B2 产物)
    versions = {m["version"] for m in data}
    assert "021_dicts.sql" in versions
    # 新 sqlite 空库, 全部未应用
    assert all(not m["applied"] for m in data)
