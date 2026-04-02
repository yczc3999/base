"""
数据库服务 — SQLAlchemy 异步引擎 + Session 管理

使用 asyncpg 驱动连接 PostgreSQL，提供：
- 全局异步引擎（连接池）
- Session 工厂（每个请求一个 Session）

使用方式（通过 FastAPI Depends 注入）：
    async def get_list(db: AsyncSession = Depends(get_db)):
        result = await db.execute(select(User))
"""

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.config import settings

# 全局异步引擎（连接池配置从 settings 读取）
engine = create_async_engine(
    settings.database_url,
    echo=settings.APP_DEBUG,                       # DEBUG 模式下打印 SQL
    pool_size=settings.DATABASE_POOL_SIZE,         # 连接池大小
    max_overflow=settings.DATABASE_MAX_OVERFLOW,   # 超出 pool_size 时的最大额外连接数
)

# Session 工厂（expire_on_commit=False：提交后不过期，避免 lazy load 报错）
async_session = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncSession:
    """
    获取数据库 Session（FastAPI 依赖注入用）

    每个请求分配一个独立的 Session，请求结束后自动关闭。
    通过 Depends(get_db) 注入到路由函数中。
    """
    async with async_session() as session:
        yield session
