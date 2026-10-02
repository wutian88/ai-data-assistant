"""Data-driven SQL answer evaluation; importing this module never calls a model."""
import ast
import json
import time
from pathlib import Path
from typing import Any, Callable

from app.core.paths import SHOP_DB
from scripts.init_demo_db import verify_demo_database

BASE_DIR = Path(__file__).resolve().parent
DATA_FILE = BASE_DIR / "datasets" / "sql_eval.json"
REPORT_FILE = BASE_DIR / "reports" / "sql_answer_report.json"


def load_cases(data_file: Path = DATA_FILE) -> dict[str, Any]:
    payload = json.loads(data_file.read_text(encoding="utf-8"))
    if not payload.get("cases"):
        raise ValueError("SQL evaluation cases must not be empty")
    return payload


def evaluate(*, data_file: Path = DATA_FILE, report_file: Path = REPORT_FILE,
             db_path: Path = SHOP_DB, safe_tool=None,
             answer_function: Callable | None = None) -> dict[str, Any]:
    payload = load_cases(data_file)
    if Path(db_path).resolve() != Path(SHOP_DB).resolve() and (
        safe_tool is None or answer_function is None
    ):
        raise ValueError("A custom evaluation database requires both explicit tool and answer dependencies")
    metadata = verify_demo_database(db_path)
    for field in ("dataset_version", "seed", "reference_date", "recent_days_inclusive"):
        if metadata.get(field) != payload.get(field):
            raise ValueError(f"SQL evaluation dataset mismatch: {field}")
    if safe_tool is None or answer_function is None:
        from app.services import sql_agent
        if safe_tool is None:
            if Path(sql_agent.DB_FILE).resolve() != Path(db_path).resolve():
                raise ValueError("SQL tool database differs from the evaluation metadata source")
            safe_tool = sql_agent.safe_sql_query
        answer_function = sql_agent.sql_answer_with_usage if answer_function is None else answer_function

    reports = []
    for case in payload["cases"]:
        record = {
            "id": case["id"],
            "question": case["question"],
            "gold_sql": case["gold_sql"],
            "time_basis": case.get("time_basis"),
            "amount_basis": case.get("amount_basis"),
            "answer_correct": None,  # Human review; never pretend keyword checks grade the model.
        }
        gold_output = safe_tool.invoke({"query": case["gold_sql"]})
        try:
            gold_rows = ast.literal_eval(gold_output)
        except (ValueError, SyntaxError) as exc:
            raise RuntimeError(f"Gold SQL failed for {case['id']}: {gold_output}") from exc
        if not isinstance(gold_rows, list) or not all(isinstance(row, tuple) for row in gold_rows):
            raise RuntimeError(f"Unexpected gold rows for {case['id']}")
        record["gold_rows"] = gold_rows
        record["gold_result"] = (
            gold_rows[0][0] if len(gold_rows) == 1 and len(gold_rows[0]) == 1 else gold_rows
        )
        started = time.perf_counter()
        try:
            result = answer_function(case["question"])
            record["agent_answer"] = result["answer"]
            record["usage"] = result["usage"]
            record["error"] = None
        except Exception as exc:
            record["agent_answer"] = None
            record["usage"] = None
            record["error"] = str(exc)
        record["latency_seconds"] = round(time.perf_counter() - started, 3)
        reports.append(record)
        print(f"[sql-eval] {case['id']} gold={record['gold_result']} error={record['error']}")

    report = {
        "dataset": metadata,
        "evaluation": {"reference_date": payload["reference_date"],
                       "recent_days_inclusive": payload["recent_days_inclusive"]},
        "cases": reports,
    }
    report_file.parent.mkdir(parents=True, exist_ok=True)
    report_file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> None:
    report = evaluate()
    print(f"[sql-eval] cases={len(report['cases'])}; report={REPORT_FILE}")
    if any(case["error"] for case in report["cases"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
