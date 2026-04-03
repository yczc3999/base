"""
队列任务基类

新建任务：在 app/jobs/ 下新建 .py 文件，继承 BaseJob，实现 handle()

示例：
    class SendSmsJob(BaseJob):
        name = "send_sms"

        async def handle(self, data: dict):
            ...

业务代码使用：
    from app.queue import Queue
    await Queue.push("send_sms", {"phone": "13800138000", "code": "1234"})
"""


class BaseJob:
    name: str = ""

    async def handle(self, data: dict):
        raise NotImplementedError
