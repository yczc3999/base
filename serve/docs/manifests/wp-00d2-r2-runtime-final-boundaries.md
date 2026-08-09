# COMPLETION MANIFEST — WP-00d2-r2 · Runtime 终态竞态与 Tracing 清理边界

- 状态：**DONE，独立审查已接受**
- 日期：2026-08-09
- 执行：Codex 审查者直接整改
- 规范：`serve/docs/tasks/wp-00d2-r2-runtime-final-boundaries.md`

## 1. 修改文件与实现

| 文件 | 实现 |
|---|---|
| `serve/app/main.py` | `configure_tracing` 纳入 lifespan `try/finally`；tracing shutdown 失败不覆盖原异常，总是清 runtime 指针 |
| `serve/app/services/runtime.py` | close 后丢弃在途 probe；DB/Redis/Artifact probe 响应类型与 bool 严格验证 |
| `serve/tests/trading/test_v2_runtime_lifecycle.py` | 新增 tracing-init/shutdown、清理顺序、health-close 竞态、四组件 malformed fail-closed 测试 |
| `serve/tests/trading/test_v2_router_registration.py` | sync initializer 使用 sync noop，消除虚假 coroutine `RuntimeWarning` |

## 2. 命令与真实结果

```text
python3 -m compileall -q app tests                         -> exit 0
pytest -q tests/trading/test_v2_artifact_factory.py       -> 7 passed
pytest -q tests/trading/test_v2_runtime_lifecycle.py      -> 35 passed
pytest -q tests/trading/test_v2_router_registration.py    -> 19 passed, 8 warnings
pytest -q tests/trading                                   -> 669 passed, 8 warnings
pytest -q                                                 -> 880 passed, 9 warnings
python -c 'import orjson; print(orjson.__version__)'       -> 3.11.9
git diff --check                                          -> exit 0
```

9 个 warning 只是既有 FastAPI/TestClient 与 pytest-asyncio 弃用告警；R1 中由 async noop 造成的
2 个 `RuntimeWarning` 已消失。

## 3. 关键证据

- tracing 初始化抛错：Runtime 未构造，legacy Redis/tracing 各关闭一次，旧 pointer 清空，
  原异常传播。
- tracing shutdown 抛错：不覆盖 body 异常，pointer 仍清空，日志无 marker。
- health probe 阻塞期间 close：晚到 probe 与 `last_snapshot` 均为 unready。
- DB truthy dict、Redis `{"ok":"non-bool"}`、Artifact `ok="non-bool"` 全部 fail-closed；Cache 只降级，
  其余 required component 使整体 unready。
- 独立审查最终结论：当前代码边界无剩余 P0/P1。

## 4. Blocker、回滚与副作用

Blocker：无。回滚：只回滚上表四个代码/测试文件的 R2 差异。无迁移、网络、
Provider 或业务数据副作用。

## 5. Manifest SHA-256

口径：删除本文件中“恰好为 64 位小写十六进制”的哈希行后计算。

```text
79998a9cc56cca8434808405d1cc1b9caa04aa4bb75bd50eb1b0df8b0dbdd05e
```
