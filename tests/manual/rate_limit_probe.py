import os
from app.core.paths import ENV_FILE, PROJECT_ROOT
import urllib.error
import urllib.request

from dotenv import load_dotenv


load_dotenv(ENV_FILE)

API_KEY = os.getenv("APP_API_KEY")

if not API_KEY:
    raise RuntimeError("APP_API_KEY 未配置")


URL = "http://127.0.0.1:8000/rate-limit-test"


for i in range(1, 7):
    request = urllib.request.Request(
        URL,
        method="GET",
        headers={
            "X-API-Key": API_KEY,
        },
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=5,
        ) as response:
            print(
                f"请求 {i}: {response.status}"
            )

    except urllib.error.HTTPError as e:
        print(
            f"请求 {i}: {e.code}"
        )

        print(
            "Retry-After:",
            e.headers.get("Retry-After"),
        )