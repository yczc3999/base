# 队列 & 定时任务设计文档

## 一、设计目标

和 PHP 版一样：**新增任务 = 新建一个类文件。**

- 定时任务：放到 `app/tasks/` 目录，声明 `interval`，自动扫描注册
- 队列任务：放到 `app/jobs/` 目录，业务代码 `await queue.push('job-name', data)`
- 零外部依赖（不用 Celery / RQ / Dramatiq），纯 asyncio + Redis
- 支持热重载（文件变更自动重新注册）

---

## 二、架构

```
┌─────────────────────────────────────────────┐
│ 主进程（uvicorn）                             │
│   - FastAPI 处理 HTTP 请求                    │
│   - 业务代码随时 queue.push() 推入队列        │
└─────────────────────────────────────────────┘

┌─────────────────────────────────────────────┐
│ Worker 进程（独立启动）                       │
│   - TaskScheduler：扫描 tasks/ 目录          │
│   - Timer：按 interval 定时推入 Redis Queue  │
│   - Consumer：从 Redis Queue 取任务执行       │
│   - 支持多 Worker 并发消费                    │
└─────────────────────────────────────────────┘

Redis Queue（List 结构）：
  base:queue:default     ← 默认队列
  base:queue:export      ← 导出队列
  base:queue:notify      ← 通知队列
  base:queue:task        ← 定时任务队列
```

---

## 三、目录结构

```
app/
├── tasks/                  # 定时任务（自动扫描）
│   ├── __init__.py
│   ├── base.py             # BaseTask 基类
│   ├── cleanup_files.py    # 示例：清理过期文件
│   └── system_monitor.py   # 示例：系统监控
│
├── jobs/                   # 队列任务
│   ├── __init__.py
│   ├── base.py             # BaseJob 基类
│   ├── send_sms.py         # 示例：发送短信
│   ├── send_notify.py      # 示例：发送通知
│   └── export_data.py      # 示例：数据导出
│
├── worker.py               # Worker 进程入口
└── queue.py                # Queue 客户端（push 方法）
```

---

## 四、定时任务

### 4.1 BaseTask

```python
class BaseTask:
    """定时任务基类"""

    name: str = ""              # 任务名称
    interval: int = 60          # 执行间隔（秒）
    enabled: bool = True        # 是否启用

    async def run(self):
        """业务逻辑（子类实现）"""
        raise NotImplementedError

    async def execute(self):
        """执行入口（带防重复 + 错误处理）"""
        cache_key = f"task:running:{self.__class__.__name__}"
        r = await get_redis()

        # 防重复执行
        if await r.get(cache_key):
            return

        await r.set(cache_key, "1", ex=self.interval)
        try:
            await self.run()
        except Exception as e:
            logger.error(f"Task {self.name} failed: {e}")
        finally:
            await r.delete(cache_key)
```

### 4.2 示例任务

```python
# app/tasks/cleanup_files.py
class CleanupFilesTask(BaseTask):
    name = "清理过期文件"
    interval = 3600  # 每小时

    async def run(self):
        # 清理 24 小时前的临时文件
        ...
```

```python
# app/tasks/system_monitor.py
class SystemMonitorTask(BaseTask):
    name = "系统监控"
    interval = 60  # 每分钟

    async def run(self):
        # 收集 CPU / 内存 / 磁盘
        # 超阈值发通知
        ...
```

### 4.3 自动扫描注册

```python
def scan_tasks() -> list[BaseTask]:
    """扫描 app/tasks/ 目录，自动发现所有 Task 类"""
    tasks = []
    task_dir = Path(__file__).parent / "tasks"
    for file in task_dir.glob("*.py"):
        if file.name.startswith("_"):
            continue
        module = importlib.import_module(f"app.tasks.{file.stem}")
        for name, cls in inspect.getmembers(module, inspect.isclass):
            if issubclass(cls, BaseTask) and cls is not BaseTask and cls.enabled:
                tasks.append(cls())
    return tasks
```

---

## 五、队列任务

### 5.1 Queue 客户端

```python
# app/queue.py
class Queue:
    """Redis 队列客户端"""

    @staticmethod
    async def push(job_name: str, data: dict, delay: int = 0):
        """
        推入队列

        :param job_name: 任务名（对应 jobs/ 目录下的类名）
        :param data:     任务数据
        :param delay:    延迟秒数（0=立即）
        """
        r = await get_redis()
        payload = json.dumps({"job": job_name, "data": data, "created_at": time.time()})

        if delay > 0:
            # 延迟队列：用 ZADD + score 实现
            await r.zadd(f"{PREFIX}:queue:delayed", {payload: time.time() + delay})
        else:
            await r.lpush(f"{PREFIX}:queue:default", payload)
```

### 5.2 业务代码使用

```python
from app.queue import Queue

# 异步发送短信（不阻塞当前请求）
await Queue.push("send_sms", {"phone": "13800138000", "code": "1234"})

# 延迟 30 秒发送通知
await Queue.push("send_notify", {"title": "订单完成", "content": "..."}, delay=30)

# 异步导出
await Queue.push("export_data", {"table": "orders", "filters": {...}, "user_id": 1})
```

### 5.3 BaseJob

```python
class BaseJob:
    """队列任务基类"""

    name: str = ""

    async def handle(self, data: dict):
        """业务逻辑（子类实现）"""
        raise NotImplementedError
```

### 5.4 示例 Job

```python
# app/jobs/send_sms.py
class SendSmsJob(BaseJob):
    name = "send_sms"

    async def handle(self, data: dict):
        from app.services.sms import sms_service
        from app.services.database import async_session

        async with async_session() as db:
            await sms_service.send_code(db, data["phone"], data["code"])
```

```python
# app/jobs/send_notify.py
class SendNotifyJob(BaseJob):
    name = "send_notify"

    async def handle(self, data: dict):
        from app.services.notify import notify_service
        from app.services.database import async_session

        async with async_session() as db:
            await notify_service.send(db, data["title"], data["content"])
```

---

## 六、Worker 进程

### 6.1 入口

```python
# app/worker.py
"""
队列 + 定时任务 Worker

启动：python -m app.worker
"""

async def main():
    # 1. 扫描定时任务
    tasks = scan_tasks()
    logger.info(f"Loaded {len(tasks)} tasks")

    # 2. 扫描队列任务
    jobs = scan_jobs()
    logger.info(f"Loaded {len(jobs)} jobs")

    # 3. 启动定时调度器
    scheduler = TaskScheduler(tasks)
    asyncio.create_task(scheduler.start())

    # 4. 启动队列消费者
    consumer = QueueConsumer(jobs)
    await consumer.start()  # 阻塞


if __name__ == "__main__":
    asyncio.run(main())
```

### 6.2 TaskScheduler

```python
class TaskScheduler:
    """定时任务调度器"""

    def __init__(self, tasks: list[BaseTask]):
        self.tasks = tasks

    async def start(self):
        """启动所有定时器"""
        for task in self.tasks:
            asyncio.create_task(self._run_loop(task))
        logger.info("TaskScheduler started")

    async def _run_loop(self, task: BaseTask):
        """单个任务的循环"""
        while True:
            try:
                await task.execute()
            except Exception as e:
                logger.error(f"Task {task.name} error: {e}")
            await asyncio.sleep(task.interval)
```

### 6.3 QueueConsumer

```python
class QueueConsumer:
    """队列消费者"""

    def __init__(self, jobs: dict[str, BaseJob]):
        self.jobs = jobs  # {"send_sms": SendSmsJob(), ...}

    async def start(self):
        """阻塞消费"""
        r = await get_redis()
        queues = [f"{PREFIX}:queue:default"]

        while True:
            # BRPOP 阻塞等待（超时 1 秒）
            result = await r.brpop(queues, timeout=1)
            if result:
                _, raw = result
                payload = json.loads(raw)
                asyncio.create_task(self._handle(payload))

            # 检查延迟队列
            await self._check_delayed()

    async def _handle(self, payload: dict):
        """处理单个任务"""
        job_name = payload.get("job")
        data = payload.get("data", {})

        job = self.jobs.get(job_name)
        if not job:
            logger.warning(f"Unknown job: {job_name}")
            return

        try:
            await job.handle(data)
        except Exception as e:
            logger.error(f"Job {job_name} failed: {e}")
            # TODO: 重试 / 死信队列

    async def _check_delayed(self):
        """检查延迟队列，到期的移入默认队列"""
        r = await get_redis()
        now = time.time()
        delayed_key = f"{PREFIX}:queue:delayed"

        items = await r.zrangebyscore(delayed_key, 0, now, start=0, num=10)
        for item in items:
            await r.zrem(delayed_key, item)
            await r.lpush(f"{PREFIX}:queue:default", item)
```

---

## 七、启动方式

```bash
# 开发环境
# 终端 1：API 服务
uvicorn app.main:app --host 0.0.0.0 --port 3000

# 终端 2：Worker 进程
python -m app.worker

# 生产环境（Supervisor 管理）
[program:base-api]
command=uvicorn app.main:app --host 0.0.0.0 --port 3000 --workers 4

[program:base-worker]
command=python -m app.worker
numprocs=2
```

---

## 八、热重载

开发环境使用 `watchfiles` 监听文件变更：

```python
# 开发模式启动
python -m app.worker --reload

# 内部实现（整进程重启，非就地重注册）：
if args.reload:
    import watchfiles
    watchfiles.run_process(
        str(app_dir / "tasks"), str(app_dir / "jobs"),
        target=lambda: asyncio.run(main()),
    )
```

生产环境不开 reload，通过 `supervisorctl restart base-worker` 重启。

---

## 九、新增任务示例

### 新增定时任务

```python
# 1. 在 app/tasks/ 下新建文件
# app/tasks/daily_report.py

from app.tasks.base import BaseTask

class DailyReportTask(BaseTask):
    name = "每日报表"
    interval = 86400  # 24 小时

    async def run(self):
        # 生成报表 + 发送通知
        ...

# 2. 完事。Worker 启动时自动扫描注册。
```

### 新增队列任务

```python
# 1. 在 app/jobs/ 下新建文件
# app/jobs/process_order.py

from app.jobs.base import BaseJob

class ProcessOrderJob(BaseJob):
    name = "process_order"

    async def handle(self, data: dict):
        order_id = data["order_id"]
        # 处理订单逻辑
        ...

# 2. 业务代码中使用
await Queue.push("process_order", {"order_id": 123})

# 3. 完事。Worker 启动时自动扫描注册。
```

---

## 十、和 PHP 版对比

| 特性 | PHP (Webman) | Python (asyncio) |
|------|-------------|-----------------|
| 定时任务 | Timer::add + Redis Queue | asyncio.sleep 循环 |
| 队列 | Webman\RedisQueue | Redis BRPOP |
| 任务定义 | 类文件 + extends Task | 类文件 + extends BaseTask |
| 自动发现 | glob + 反射 | importlib + inspect |
| 防重复 | Cache key | Redis SET NX |
| 延迟队列 | Redis Queue delay 参数 | Redis ZADD + score |
| 热重载 | ❌ TODO | ✅ watchfiles |
| 多 Worker | ✅ worker_id=0 | ✅ 多进程消费 |
| 依赖 | webman/redis-queue | 零（标准库 + redis） |
