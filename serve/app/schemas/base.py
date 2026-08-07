"""
通用响应模型 — 与 app/utils/response.py 的 {code, msg, data} 契约一一对应。

- ApiResponse[T]  通用信封：code/msg/data，T 为 data 的具体类型
- ListData        getList 的 data 形状：{list, total, page, pageSize}
- ListResponse    getList 完整响应：ApiResponse[ListData]
- DetailResponse  getDetail 完整响应：ApiResponse[dict]（单行数据）

用法（crud_router 自动生成接口）：
    @router.get("/getList", response_model=ListResponse)
    async def get_list(...):
        return ok(await logic.get_list(db, query))

扩展：新增模块需要自己的响应形状时，在 app/schemas/ 下新建模型，
用 `ApiResponse[YourModel]` 组合即可，无需改 base.py。
"""
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    """统一响应信封 {code, msg, data}"""

    code: int = 0
    msg: str = "success"
    data: T | None = None

    model_config = ConfigDict(extra="ignore")


class ListData(BaseModel):
    """getList 的 data 部分。

    注意：字段名避开内置 `list`（Pydantic v2 不接受名为 list 的字段），
    用 alias="list" 保持对外 JSON 键名不变（前端依赖 data.list）。
    """

    rows: list[Any] = Field(default_factory=list, alias="list")
    total: int = 0
    page: int = 1
    pageSize: int = 20

    model_config = ConfigDict(extra="ignore", populate_by_name=True)


# getList / getDetail 具体化响应模型
ListResponse = ApiResponse[ListData]
DetailResponse = ApiResponse[dict]
