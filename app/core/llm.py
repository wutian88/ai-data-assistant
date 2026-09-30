import os

from app.core.paths import PROJECT_ROOT, ENV_PATH
from langchain_openai import ChatOpenAI
from langchain_community.embeddings import DashScopeEmbeddings


# ============================================================
# 1. 环境变量
#
# 统一使用项目根目录：
#
# My_FastAPI/.env
#
# Docker 中如果环境变量已经由 compose 注入，
# os.getenv() 同样可以直接读取。
# ============================================================

# ============================================================
# 2. DashScope API Key
# ============================================================

DASHSCOPE_API_KEY = os.getenv(
    "DASHSCOPE_API_KEY"
)

if not DASHSCOPE_API_KEY:
    raise RuntimeError(
        "DASHSCOPE_API_KEY 未配置，请检查环境变量或项目根目录 .env"
    )


# ============================================================
# 3. Chat Model
# ============================================================

model = ChatOpenAI(
    model="qwen3.7-flash-2026-07-15",
    api_key=DASHSCOPE_API_KEY,
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    temperature=0,
)


# ============================================================
# 4. Embedding Model
# ============================================================

embeddings = DashScopeEmbeddings(
    model="text-embedding-v4",
    dashscope_api_key=DASHSCOPE_API_KEY,
)