"""
ArtifactStore 唯一 factory（WP-00d2）。

- `build_artifact_store(cfg, *, s3_client=None)`：`ARTIFACT_DRIVER=local` → 构造
  `LocalArtifactDriver(root)`；`s3` → 复用现有 `build_s3_artifact_driver(cfg, client=s3_client)`
  （不复制 S3 Config/retry/signature 逻辑）；其他值 fail-fast。
- 统一返回 `ArtifactStore(driver, cfg)`。
- import 本模块零网络：构造 driver 只解析路径/创建本地目录，S3 client 由 builder 在调用时创建。
"""

from __future__ import annotations

from app.config import Settings
from app.services.artifact_store import ArtifactStore
from app.services.artifact_store.drivers.local import LocalArtifactDriver
from app.services.artifact_store.drivers.s3 import build_s3_artifact_driver


def build_artifact_store(cfg: Settings, *, s3_client: object | None = None) -> ArtifactStore:
    """从 Settings 构建 ArtifactStore。s3_client 仅用于注入测试；缺省由 builder 建真实 client。"""
    driver_name = cfg.ARTIFACT_DRIVER
    if driver_name == "local":
        driver = LocalArtifactDriver(cfg.ARTIFACT_LOCAL_ROOT)
    elif driver_name == "s3":
        driver = build_s3_artifact_driver(cfg, client=s3_client)
    else:
        raise ValueError(
            f"ARTIFACT_DRIVER must be 'local' or 's3', got {driver_name!r}"
        )
    return ArtifactStore(driver, cfg)
