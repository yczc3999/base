"""
P2-6 前端用户消息测试 — 发送/列表(按用户过滤)/未读/已读

覆盖:
  1. send 创建消息
  2. get_list 按 user_id 过滤 (bind_user_column)
  3. unread_count
  4. mark_read / mark_all_read
  5. 不能读别人的消息 (bind 隔离)
"""

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.base import Base
from app.models.message import Message
from app.logics.message import message_logic


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda c: Base.metadata.create_all(c, tables=[Message.__table__])
        )
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_send_and_list_by_user(db, mock_redis):
    # 给两个用户各发消息
    await message_logic.send(db, user_id=1, title="欢迎", content="欢迎加入")
    await message_logic.send(db, user_id=1, title="订单", content="订单已发货")
    await message_logic.send(db, user_id=2, title="公告", content="系统公告")

    # 用户 1 只能看到自己的
    r1 = await message_logic.get_list(db, {}, user_id=1)
    assert r1["total"] == 2
    assert all(m["user_id"] == 1 for m in r1["list"])

    r2 = await message_logic.get_list(db, {}, user_id=2)
    assert r2["total"] == 1


@pytest.mark.asyncio
async def test_unread_and_mark_read(db, mock_redis):
    await message_logic.send(db, user_id=1, title="a")
    await message_logic.send(db, user_id=1, title="b")

    assert await message_logic.unread_count(db, 1) == 2

    # 标记单条已读
    msgs = (await message_logic.get_list(db, {}, user_id=1))["list"]
    await message_logic.mark_read(db, msgs[0]["id"], user_id=1)
    assert await message_logic.unread_count(db, 1) == 1

    # 全部已读
    await message_logic.mark_all_read(db, user_id=1)
    assert await message_logic.unread_count(db, 1) == 0


@pytest.mark.asyncio
async def test_mark_read_cannot_touch_others(db, mock_redis):
    """mark_read 带 user_id 归属校验: 用户1 不能标记用户2 的消息"""
    await message_logic.send(db, user_id=2, title="u2 的消息")

    msgs = (await message_logic.get_list(db, {}, user_id=2))["list"]
    msg_id = msgs[0]["id"]

    # 用户 1 尝试标记用户 2 的消息 → where 条件不匹配, 不影响
    await message_logic.mark_read(db, msg_id, user_id=1)
    assert await message_logic.unread_count(db, 2) == 1
