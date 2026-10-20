import os

from app.core.paths import PROJECT_ROOT, ENV_FILE


# ============================================================
# 1. 项目环境变量
#
# 本地开发：
#   读取项目根目录 .env
#
# Docker：
#   环境变量由 compose.yaml 注入，
#   即使容器中不存在 .env 文件也可以正常读取。
# ============================================================

# ============================================================
# 2. 应用鉴权
# ============================================================

APP_API_KEY = os.getenv(
    "APP_API_KEY"
)

if not APP_API_KEY:
    raise RuntimeError(
        "APP_API_KEY 未配置，请检查环境变量或项目根目录 .env"
    )


# ============================================================
# 3. 并发控制
# ============================================================

MAX_CONCURRENT_REQUESTS = int(
    os.getenv(
        "MAX_CONCURRENT_REQUESTS",
        "4",
    )
)

CONCURRENCY_WAIT_SECONDS = float(
    os.getenv(
        "CONCURRENCY_WAIT_SECONDS",
        "0.2",
    )
)


# ============================================================
# 4. Rate Limit
# ============================================================

RATE_LIMIT_REQUESTS = int(
    os.getenv(
        "RATE_LIMIT_REQUESTS",
        "20",
    )
)

RATE_LIMIT_WINDOW_SECONDS = int(
    os.getenv(
        "RATE_LIMIT_WINDOW_SECONDS",
        "60",
    )
)

# Request budget includes route classification plus the selected business path.
# The MCP boundary remains 45s; individual provider calls are shorter.
END_TO_END_TIMEOUT_SECONDS = float(os.getenv("END_TO_END_TIMEOUT_SECONDS", "90"))
MODEL_CALL_TIMEOUT_SECONDS = float(os.getenv("MODEL_CALL_TIMEOUT_SECONDS", "20"))
MODEL_RETRY_ATTEMPTS = int(os.getenv("MODEL_RETRY_ATTEMPTS", "2"))
MCP_TIMEOUT_SECONDS = 45

if END_TO_END_TIMEOUT_SECONDS <= 0 or MODEL_CALL_TIMEOUT_SECONDS <= 0:
    raise RuntimeError("Request and model timeout settings must be positive")
if MODEL_CALL_TIMEOUT_SECONDS < 20 or MODEL_CALL_TIMEOUT_SECONDS > 30:
    raise RuntimeError("MODEL_CALL_TIMEOUT_SECONDS must be between 20 and 30")
if MODEL_RETRY_ATTEMPTS < 1 or MODEL_RETRY_ATTEMPTS > 2:
    raise RuntimeError("MODEL_RETRY_ATTEMPTS must be 1 or 2")
if END_TO_END_TIMEOUT_SECONDS <= MCP_TIMEOUT_SECONDS + MODEL_CALL_TIMEOUT_SECONDS * MODEL_RETRY_ATTEMPTS + 1:
    raise RuntimeError("END_TO_END_TIMEOUT_SECONDS must exceed the MCP and model retry budgets")
