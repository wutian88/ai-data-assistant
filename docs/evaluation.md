# 评测状态与复现

2026-10-15：本轮按用户要求只做离线开发，不调用付费模型。评测脚本已提供，尚未形成本轮真实模型评测结果。没有模型通过数、失败数或准确率，也没有新增公开的模型结果 CSV。`evals/examples/` 的旧输出不能作为本轮结果。

## 环境与题集

- 基线提交：`2fe3548a0ecf7c265c3f19c101374c19cd466b87`。
- 本地 Python：3.13.3。
- 代码配置的聊天模型：`qwen3.7-flash-2026-07-15`；Embedding：`text-embedding-v4`。这是静态配置，本轮未调用或验证真实服务。
- 没有读取、复制或发布真实 `.env`，离线测试使用假凭据、临时数据库和网络访问阻断。

| 类别 | 版本化题集条数 | 本轮真实模型运行 | 通过数 / 失败数 |
| --- | ---: | --- | --- |
| 路由 | 10 | 未运行 | 未统计 |
| RAG 检索 | 3 | 未运行 | 未统计 |
| RAG 答案 | 3 | 未运行 | 未统计 |
| SQL 答案 | 16 | 未运行 | 未统计 |

条数来自 `evals/datasets/*.json`，不代表执行次数或通过数。题集由项目作者自拟且样本小，不能外推生产质量。RAG 来源命中不等于答案忠实，关键词检查不等于完整评分。SQL 的 `answer_correct` 是待人工填写的判断，未填写时必须保持 `null`。

## 已完成的离线验证

在项目根目录运行：

```powershell
python -B -m pytest tests/test_demo_database.py -q -p no:cacheprovider
```

本轮输出摘要：`31 passed, 2 warnings in 4.83s`。两个警告来自现有 langchain-community、DashScope 依赖的弃用提示。

其中 `test_business_gold_queries_use_actual_data_and_fixed_time` 遍历全部 16 条 gold SQL，对比安全工具执行结果与固定演示库的实际查询结果；`test_sql_evaluation_import_is_inert_and_mocked_run_uses_real_gold` 使用 Mock 答案验证评测报告结构，模型人工评分仍为 `null`。**这不是 SQL Agent 的 16 题回答评分，也不是 16 次真实模型调用。**其他测试检查演示数据与安全升级行为。

这组离线检查没有失败样例；真实模型评测尚未运行，因此没有可报告的模型失败样例。

## 后续真实评测命令

下列命令会调用真实模型或 Embedding 服务。需要另行确认凭据和费用预算，本轮没有执行：

```powershell
python -m evals.router
python -m evals.rag_retrieval
python -m evals.rag_answers
python -m evals.sql_answers
```

原始输出位于 Git 忽略的 `evals/reports/`：前三项写 CSV，SQL 答案写 `sql_answer_report.json`。执行时保留完整终端输出、提交、依赖和模型配置、日期；原样保留失败样例，人工复核答案，再把脱敏汇总保存为 `docs/eval_results_<日期>.csv`。每个统计必须包含对应题数和通过数，不能将 gold SQL 校验或 Mock 输出改写成模型评分。

当前求职材料可写：“提供路由、RAG 检索与答案、SQL 答案评测脚本；已完成离线程序验证，尚未形成真实模型评测结果。”
