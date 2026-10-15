# SSE 离线验证

日期：2026-10-15。基线 `2fe3548`，Python 3.13.3。本轮没有调用付费模型，也没有更改 SQL 校验规则。

## 自动化检查

```powershell
python -B -m pytest tests/test_streaming.py -q -p no:cacheprovider
python -B -m pytest -q -p no:cacheprovider
python -B -m scripts.run_regression
```

SSE 专项：`13 passed, 2 warnings in 2.54s`。

全量：`110 passed, 2 warnings in 71.23s (0:01:11)`。其中原有 97 项无回退，新增 13 项；8 个回归模块包装用例已计入总数，不能另加。独立回归输出：

```text
测试模块：8
通过模块：8
失败模块：0
全部回归测试通过
```

随后完成命名清理与旧部署兼容，再执行相同两条全量命令：`121 passed, 2 warnings in 28.18s`，独立回归仍为 8/8 通过。新增 11 项针对新旧凭据、无效配置、旧文件保留和 marker 选择；SSE 专项仍是 13 项。110 是 SSE 提交阶段的记录，121 是最终命名清理后的记录。

Claude 复核后补充线程归属库与 checkpoint 成对选择的 4 项检查，最终再次运行：`125 passed, 2 warnings in 51.31s`，独立八模块回归 8/8 通过。最终总数为 125，命名兼容 15 项；上文 110、121 保留为开发阶段记录。

新增检查覆盖 RAG / SQL / fallback 事件顺序、换行与 Unicode 文本拼接、内部工具数据不外泄、节点异常及图内错误终止、401、流开始前 429、请求校验、原 `/ask` JSON 行为、真实 LangGraph 更新、ASGI 2.4 计算中和答案发送中断开、传输发送异常、并发名额持续持有及释放后重试。模型和文档来源使用 Mock；HTTP 路由、依赖、SSE 编码和取消链路使用实际代码。

## 本机 HTTP 实测

服务端（只绑定回环地址，外部网络阻断，使用公开假凭据与临时数据）：

```powershell
python -B -m tests.manual.sse_offline_demo --port 18765 --duration 30
```

另一个终端：

```powershell
curl.exe --no-buffer --silent --show-error --max-time 10 -H 'Content-Type: application/json' -H 'X-API-Key: offline-app-key' --data '{"question":"demo"}' http://127.0.0.1:18765/ask/stream
```

实际输出：

```text
event: status
data: {"stage": "routing"}

event: status
data: {"stage": "retrieving"}

event: delta
data: {"text": "离线演示：图执行完成后分块发送最终答案。\n离线演示：图执行完成后分块发送最终答案。\n离线演示：图执行完成后分块发送最终答案。\n离线演示：图执行完成后分块发送最终"}

event: delta
data: {"text": "答案。\n离线演示：图执行完成后分块发送最终答案。\n离线演示：图执行完成后分块发送最终答案。\n"}

event: done
data: {"status": "completed", "request_id": "ddf4abcf-95e5-423b-985a-99aa6ffe3902", "route": "rag", "sources": ["Mock 文档"], "usage": {"input_tokens": null, "output_tokens": null, "total_tokens": null, "reasoning_tokens": null}}
```

curl `--trace-time --trace-ascii` 记录的接收时点（北京时间）：routing 16:04:52.397，retrieving 16:04:52.561，第一个 delta 16:04:52.823，第二个 delta 16:04:52.824，done 16:04:52.825。服务器对应日志为 first_status 0.000 秒、first_delta 0.426 秒、stream end 0.429 秒。这些延迟来自带固定等待的 Mock 图，不能作为模型延迟或性能提升。

## 表达边界

这是阶段状态和完整答案的分块推送，不是模型 token 级流式。SQL 和工具参数不流式执行。验证对象是本机直接 HTTP，未测试 Nginx、CDN、网关或 Docker 镜像，也未压测、核验远程 CI 或运行真实模型评测。既有 HTTP middleware 的耗时在响应头返回时记录，完整流时长应查看 `stream end`，不能混为一项。

客户端断开会取消本地图消费并释放名额，但不能保证第三方厂商立即停止计算或计费。进程内限流、并发和线程锁的原有范围保持不变。

当前未设专用的流总时限或发送超时，慢客户端可能长期占用名额；真实 MCP 子进程的断开清理没有做专项实测。`/threads` 尚无流式接口，Streamlit 仍用 `/ask`，本轮只用 curl 演示 SSE。Compose 静态检查使用公开 `.env.example` 的覆盖配置通过，未读取私密 `.env`，没有构建或启动容器。
