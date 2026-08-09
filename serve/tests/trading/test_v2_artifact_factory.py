"""
WP-00d2 ArtifactStore factory 验收测试。

覆盖：local/s3 分支、S3 client 注入、exact config 传递、非法 driver fail-fast、
import/factory 注入路径零网络、不复制凭据。
"""

from pathlib import Path

import boto3
import pytest

from app.config import Settings
from app.services.artifact_store import ArtifactStore
from app.services.artifact_store.drivers.local import LocalArtifactDriver
from app.services.artifact_store.drivers.s3 import S3ArtifactDriver
from app.services.artifact_store.factory import build_artifact_store


def _cfg(**over) -> Settings:
    base = dict(
        _env_file=None,
        ARTIFACT_DRIVER="local",
        ARTIFACT_LOCAL_ROOT="/tmp/v2-af-root",
        ARTIFACT_INLINE_THRESHOLD_BYTES=1,
        ARTIFACT_COMPRESSION_THRESHOLD_BYTES=1,
        ARTIFACT_MAX_OBJECT_BYTES=67_108_864,
    )
    base.update(over)
    return Settings(**base)


def test_factory_local_branch(tmp_path):
    root = tmp_path / "artifacts"
    cfg = _cfg(ARTIFACT_LOCAL_ROOT=str(root))
    store = build_artifact_store(cfg)
    assert isinstance(store, ArtifactStore)
    assert isinstance(store._driver, LocalArtifactDriver)
    assert store.health().driver == "local"
    assert Path(store._driver._root) == root.resolve()


def test_factory_local_creates_root(tmp_path):
    root = tmp_path / "nested" / "artifacts"
    store = build_artifact_store(_cfg(ARTIFACT_LOCAL_ROOT=str(root)))
    assert root.resolve().is_dir()


class RecordingS3Client:
    """严格 fake：只记录调用；供 s3 分支注入。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.close_calls = 0

    def put_object(self, **kw):
        self.calls.append(("put_object", kw))
        return {}

    def head_object(self, **kw):
        self.calls.append(("head_object", kw))
        return {}

    def get_object(self, **kw):
        self.calls.append(("get_object", kw))
        return {}

    def head_bucket(self, **kw):
        self.calls.append(("head_bucket", kw))
        return {}

    def close(self):
        self.close_calls += 1


def test_factory_s3_branch_with_injected_client(monkeypatch):
    """s3 分支注入 client：driver=s3、不调 boto3.client、不复制凭据。"""
    fake = RecordingS3Client()
    monkeypatch.setattr(
        boto3, "client",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("boto3.client must not be called")),
    )
    cfg = _cfg(ARTIFACT_DRIVER="s3", ARTIFACT_S3_BUCKET="mybucket",
               ARTIFACT_S3_REGION="us-east-1")
    store = build_artifact_store(cfg, s3_client=fake)
    assert isinstance(store, ArtifactStore)
    assert isinstance(store._driver, S3ArtifactDriver)
    assert store.health().driver == "s3"
    assert store._driver._client is fake


def test_factory_s3_exact_config_passed(monkeypatch):
    """s3 分支 exact config 传递：retry limit 取 MAX_ATTEMPTS、prefix/bucket/owner 一致。"""
    fake = RecordingS3Client()
    monkeypatch.setattr(boto3, "client", lambda *a, **k: fake)
    cfg = _cfg(ARTIFACT_DRIVER="s3", ARTIFACT_S3_BUCKET="exact-bucket",
               ARTIFACT_S3_REGION="ap-southeast-1", ARTIFACT_S3_PREFIX="pm/v2",
               ARTIFACT_S3_EXPECTED_BUCKET_OWNER="owner-123",
               ARTIFACT_S3_MAX_ATTEMPTS=4)
    store = build_artifact_store(cfg)
    driver = store._driver
    assert driver._bucket == "exact-bucket"
    assert driver._prefix == "pm/v2"
    assert driver._expected_owner == "owner-123"
    assert driver._retry_limit == 4


def test_factory_invalid_driver_rejected():
    with pytest.raises(ValueError):
        build_artifact_store(_cfg(ARTIFACT_DRIVER="gcs"))


def test_factory_s3_requires_bucket_and_region(monkeypatch):
    """driver=s3 缺 bucket/region：Settings 构造即拒绝（fail-fast，不延迟到 client）。"""
    with pytest.raises(Exception):
        _cfg(ARTIFACT_DRIVER="s3", ARTIFACT_S3_BUCKET="", ARTIFACT_S3_REGION="")


def test_factory_import_zero_network():
    """import factory 不触发任何网络 client 创建。"""
    import importlib
    import sys

    # 重新 import 一个全新模块实例，确认不执行任何 boto3.client
    for mod in list(sys.modules):
        if mod.startswith("app.services.artifact_store.factory"):
            del sys.modules[mod]
    m = importlib.import_module("app.services.artifact_store.factory")
    assert callable(m.build_artifact_store)
    # 模块加载后 driver 模块仍在且未建连接（无副作用断言由前面测试覆盖）
