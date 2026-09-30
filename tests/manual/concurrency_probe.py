from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import time
import urllib.error
import urllib.request

import os
from app.core.paths import ENV_FILE, PROJECT_ROOT

from dotenv import load_dotenv



load_dotenv(ENV_FILE)

API_KEY = os.getenv("APP_API_KEY")

if not API_KEY:
    raise RuntimeError("APP_API_KEY 未配置")
URL = "http://127.0.0.1:8000/ask"


REQUEST_COUNT = 5


def send_request(index: int):
    body = {
        "question": "公司的退款规则是什么？"
    }

    data = json.dumps(
        body,
        ensure_ascii=False,
    ).encode("utf-8")

    request = urllib.request.Request(
        URL,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "X-API-Key": API_KEY,
        },
    )

    start = time.perf_counter()

    try:
        with urllib.request.urlopen(
            request,
            timeout=60,
        ) as response:

            result = response.read().decode(
                "utf-8",
                errors="replace",
            )

            elapsed = time.perf_counter() - start

            return (
                index,
                response.status,
                elapsed,
                result[:100],
            )

    except urllib.error.HTTPError as e:
        result = e.read().decode(
            "utf-8",
            errors="replace",
        )

        elapsed = time.perf_counter() - start

        return (
            index,
            e.code,
            elapsed,
            result[:100],
        )

    except Exception as e:
        elapsed = time.perf_counter() - start

        return (
            index,
            "ERROR",
            elapsed,
            repr(e),
        )


def main():
    print(
        f"同时发送 {REQUEST_COUNT} 个请求..."
    )

    with ThreadPoolExecutor(
        max_workers=REQUEST_COUNT
    ) as executor:

        futures = [
            executor.submit(
                send_request,
                i,
            )
            for i in range(1, REQUEST_COUNT + 1)
        ]

        for future in as_completed(futures):
            index, status, elapsed, result = (
                future.result()
            )

            print(
                f"请求 {index}: "
                f"status={status}, "
                f"time={elapsed:.2f}s"
            )

            print(
                f"response={result}\n"
            )


if __name__ == "__main__":
    main()