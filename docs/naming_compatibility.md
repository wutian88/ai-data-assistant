# 项目命名与旧部署兼容

2026-10-15：API 标题、MCP 工具服务名、会话标签、注释、测试数据来源名采用 AI Data Assistant 或具体模块名；示例环境变量、CI、测试与 README 同步更新。

新会话配置为 `SESSION_USER_A_KEY` 与 `SESSION_USER_B_KEY`。没有配置任何新变量时，才整对回退到旧变量；新变量仅设置一项、为空或重复，仍返回配置错误，不混用新旧凭据。身份固定映射、定时安全比较、读写线程归属、API Key 及 SQL 校验规则没有放宽。真实 `.env` 保持原状，Compose 的 `env_file` 继续注入配置。

新数据名为 `milvus.db`、`thread_owners.db`、`session_checkpoints.db` 和 `.milvus_initialized`。旧文件原地读取，不自动移动或删除；向量库新旧同时存在时选择新名，marker 跟随选中的库。线程归属库和 checkpoint 成对选择同一代：有完整新对时整对用新名，只有旧数据时整对用旧名；新对不完整且有旧数据则拒绝启动。新旧线程库都存在时记录选择警告。这是读取兼容，不是自动数据迁移。

`rg -n -i 'Day14|Day18|Day19' app tests README.md .env.example .github compose.yaml` 的剩余命中仅用于旧配置/旧文件读取、对应测试假凭据与兼容文档。保留这些字符串是为了已有部署持续可用。

验证：

```powershell
python -B -m pytest tests/test_runtime_compatibility.py tests/test_streaming.py -q -p no:cacheprovider
python -B -m pytest -q -p no:cacheprovider
python -B -m scripts.run_regression
git diff --check
```

最终全量为 `125 passed, 2 warnings in 51.31s`；独立回归 8 个模块全部通过。总数中含 15 项命名兼容测试、13 项 SSE 测试。检查包括无效的新凭据不会回退、旧配置可用、新配置整对优先、既有文件内容保留、marker 不跨库代次误用，以及归属库和 checkpoint 成对选择、混合不完整布局拒绝启动。

本轮没有推送或核验远程 GitHub Actions。README 不再沿用“尚未 commit”描述，远程结果以 Actions 页面为准。
