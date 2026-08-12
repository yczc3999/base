"""V2 Admin Read API typed DTO（WP-07A Checkpoint A）。

- BIGINT id 为十进制字符串、NUMERIC 为 decimal string、时间为 UTC ISO、hash 为 64-hex。
- 列表统一 envelope：``{items, next_cursor, has_more, as_of, filter_hash}``。
- 不返回 page/pageSize/total；统一 response 继续使用 Base ``{code,msg,data}``。
- authoritative 事实字段带 ``authoritative=True`` 与明确 ``as_of``。
"""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class CursorPage(BaseModel, Generic[T]):
    """列表统一响应体（keyset；无 OFFSET/COUNT）。"""

    model_config = ConfigDict(extra="forbid")

    items: list[T]
    next_cursor: str | None = None
    has_more: bool = False
    as_of: str
    filter_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class Authoritative(BaseModel):
    """权威事实标记：金额/数量为 decimal string，且带明确 read snapshot。"""

    model_config = ConfigDict(extra="forbid")

    authoritative: bool = True
    as_of: str


class ProjectionBlock(BaseModel):
    """Dashboard projection 块元数据（只读五张 projection）。"""

    model_config = ConfigDict(extra="forbid")

    as_of: str
    source_high_watermark: str | None = None
    projection_version: int | None = None
    projection_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    freshness_status: str = Field(pattern="^(fresh|stale|missing)$")
    rows: list[dict] = Field(default_factory=list)


class DashboardResponse(BaseModel):
    """Dashboard 只读五张 projection + authoritative 摘要。"""

    model_config = ConfigDict(extra="forbid")

    blocks: dict[str, ProjectionBlock]
    as_of: str


class DecisionTraceItem(BaseModel):
    """execution/{decision_id}/trace 的链上项。"""

    model_config = ConfigDict(extra="forbid")

    kind: str
    id: str
    ref: dict | None = None


class ArtifactMetadata(BaseModel):
    """artifact metadata（content 分离；无存储路径/凭证）。"""

    model_config = ConfigDict(extra="forbid")

    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_type: str
    content_length: str = Field(pattern=r"^\d+$")
    lineage: list[dict] = Field(default_factory=list)
    stored_at: str | None = None


class ArtifactContentResponse(BaseModel):
    """artifact content Range 响应（Controller 组装 206/416）。"""

    model_config = ConfigDict(extra="forbid")

    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_type: str
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    total: int = Field(ge=0)
    etag: str
