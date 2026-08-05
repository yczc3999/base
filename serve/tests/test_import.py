"""
P1-3 数据导入测试 — 模板生成 / 解析 / 逐行入库 / 模块解析

覆盖:
  1. build_template_bytes 生成模板 (表头=中文标签)
  2. parse_rows 解析真实数据行 (跳过注释行/空行)
  3. import_rows 逐行独立事务 (单行失败不影响其他)
  4. resolve_logic_module 白名单解析 + 拒绝未知模块
"""

import io
import pytest
import pytest_asyncio
from openpyxl import Workbook
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.logics.base import BaseLogic, BizError
from app.models.base import Base
from app.models.dict import Dict, DictItem
from app.utils.import_helper import (
    build_template_bytes, parse_rows, import_rows, resolve_logic_module,
)


class _TplLogic(BaseLogic):
    model = None
    cache_prefix = ""

    def export_header_map(self):
        return {"username": "用户名", "status": "状态", "nickname": "昵称"}


# ==================== 模板 + 解析 ====================

def test_build_template_and_parse_empty():
    logic = _TplLogic()
    content = build_template_bytes(logic)
    assert content  # 非空 xlsx
    rows = parse_rows(logic, content)
    assert rows == []  # 只有表头+注释行


def test_parse_rows_skips_comment_and_empty():
    logic = _TplLogic()
    wb = Workbook()
    ws = wb.active
    ws.append(["用户名", "状态", "昵称"])           # 表头（标签）
    ws.append(["字段名: username", "字段名: status", "字段名: nickname"])  # 注释行
    ws.append(["张三", 1, "三三"])                  # 数据 1
    ws.append(["李四", 0, "四四"])                  # 数据 2
    ws.append([None, None, None])                  # 空行（跳过）

    buf = io.BytesIO()
    wb.save(buf)
    rows = parse_rows(logic, buf.getvalue())

    assert rows == [
        {"username": "张三", "status": 1, "nickname": "三三"},
        {"username": "李四", "status": 0, "nickname": "四四"},
    ]


def test_parse_rows_unknown_header_ignored():
    """模板里多出来的列（不在 export_header_map 中）被忽略"""
    logic = _TplLogic()
    wb = Workbook()
    ws = wb.active
    ws.append(["用户名", "状态", "随机列"])          # 第三列是未知标签
    ws.append(["张三", 1, "随便"])
    buf = io.BytesIO()
    wb.save(buf)
    rows = parse_rows(logic, buf.getvalue())
    assert rows == [{"username": "张三", "status": 1}]


# ==================== 逐行入库 ====================

@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(text("PRAGMA foreign_keys=ON"))
        await conn.run_sync(
            lambda c: Base.metadata.create_all(c, tables=[Dict.__table__, DictItem.__table__])
        )
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_import_rows_per_row_isolation(db, mock_redis_cache):
    from app.logics.dict import DictLogic, DictItemLogic
    dict_logic = DictLogic()
    logic = DictItemLogic()
    d = await dict_logic.create(db, {"type_name": "gender"})

    rows = [
        {"dict_id": d["id"], "value": "1", "label": "男"},    # 成功
        {"dict_id": d["id"], "value": "2", "label": "女"},    # 成功
        {"dict_id": d["id"], "value": "3"},                    # 缺 label → 校验失败
    ]
    result = await import_rows(db, logic, rows)

    assert result["imported"] == 2
    assert result["failed"] == 1
    assert len(result["errors"]) == 1
    assert "label" in result["errors"][0]["error"]  # 错误含字段信息


# ==================== 模块解析 ====================

def test_resolve_logic_module_dict():
    from app.logics.dict import DictLogic
    logic = resolve_logic_module("dict")
    assert isinstance(logic, DictLogic)


def test_resolve_unknown_module_rejected():
    with pytest.raises(BizError):
        resolve_logic_module("not_exist_module")
