import json
from pathlib import Path
from typing import Any, Dict, List


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    records = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                records.append(record)
    return records


def collect_trace_ids(records: List[Dict[str, Any]]) -> List[str]:
    trace_ids = []
    for record in records:
        values = record.get("trace_ids")
        candidates = values if isinstance(values, list) else [record.get("trace_id")]
        for candidate in candidates:
            if candidate and candidate not in trace_ids:
                trace_ids.append(str(candidate))
    return trace_ids


def first_present(records: List[Dict[str, Any]], key: str) -> str:
    for record in records:
        value = record.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def last_present(records: List[Dict[str, Any]], key: str) -> Any:
    value = None
    for record in records:
        candidate = record.get(key)
        if candidate not in (None, ""):
            value = candidate
    return value


def normalize_failure_category(success: bool | None, failure_category: Any) -> str:
    return "none" if success is True else str(failure_category or "unknown")


def summarize_run(path: Path) -> Dict[str, Any]:
    records = read_jsonl(path)
    final = next((record for record in reversed(records) if record.get("event") in {"observe", "failure"}), {})
    status = final.get("status")
    raw_failure_category = final.get("failure_category") or final.get("error_type") or "unknown"
    success = True if status == "completed" else False if final else None
    failure_category = normalize_failure_category(success, raw_failure_category)
    objective = first_present(records, "task")
    trace_ids = collect_trace_ids(records)
    timestamp = first_present(records, "timestamp")

    experiment = first_present(records, "experiment") or "custom"

    return {
        "run_id": path.stem,
        "filename": path.name,
        "timestamp": timestamp,
        "objective": objective,
        "success": success,
        "failure_category": failure_category,
        "trace_count": len(trace_ids),
        "experiment": experiment,
        "modified": path.stat().st_mtime,
    }


def build_attempts(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    attempts = []
    for record in records:
        if record.get("event") not in {"observe", "failure"}:
            continue
        status = record.get("status")
        success = True if status == "completed" else False
        failure_category = record.get("failure_category") or record.get("error_type")
        trace_ids = record.get("trace_ids")
        if isinstance(trace_ids, list):
            trace_id = trace_ids[0] if trace_ids else record.get("trace_id")
        else:
            trace_id = record.get("trace_id")
        observation = record.get("error") or record.get("stderr") or status or record.get("event", "")
        attempts.append(
            {
                "attempt": record.get("attempt", len(attempts) + 1),
                "success": success,
                "failure_category": normalize_failure_category(success, failure_category),
                "trace_id": trace_id or "",
                "stdout": record.get("stdout", ""),
                "stderr": record.get("stderr") or record.get("error") or "",
                "observation": observation,
            }
        )
    return attempts


def build_run(path: Path) -> Dict[str, Any]:
    records = read_jsonl(path)
    summary = summarize_run(path)
    final = next((record for record in reversed(records) if record.get("event") in {"observe", "failure"}), {})
    attempts = []
    for record in records:
        attempt = record.get("attempt")
        if attempt is not None and attempt not in attempts:
            attempts.append(attempt)

    return {
        **summary,
        "attempts": len(attempts),
        "attempt_blocks": build_attempts(records),
        "trace_ids": collect_trace_ids(records),
        "observations": [
            record.get("error") or record.get("stderr") or record.get("status") or record.get("event", "")
            for record in records
            if record.get("event") in {"observe", "failure"}
        ],
        "stdout": last_present(records, "stdout") or "",
        "stderr": last_present(records, "stderr") or last_present(records, "error") or "",
        "raw_records": records,
    }


def get_run(log_dir: Path, run_id: str) -> Dict[str, Any] | None:
    if not run_id or "/" in run_id or "\\" in run_id:
        return None
    for path in log_dir.glob("*.jsonl"):
        if path.is_file() and path.stem == run_id:
            return build_run(path)
    return None


def list_runs(log_dir: Path) -> List[Dict[str, Any]]:
    runs = []
    for path in log_dir.glob("*.jsonl"):
        if path.is_file():
            runs.append(summarize_run(path))
    return sorted(runs, key=lambda item: (item.get("timestamp") or "", item["modified"]), reverse=True)


def list_experiments(log_dir: Path) -> List[Dict[str, Any]]:
    grouped: Dict[str, Dict[str, Any]] = {}
    for run in list_runs(log_dir):
        name = run["experiment"]
        item = grouped.setdefault(
            name,
            {
                "name": name,
                "run_count": 0,
                "success_count": 0,
                "failure_count": 0,
                "last_run_timestamp": "",
            },
        )
        item["run_count"] += 1
        if run["success"] is True:
            item["success_count"] += 1
        elif run["success"] is False:
            item["failure_count"] += 1
        if run["timestamp"] and run["timestamp"] > item["last_run_timestamp"]:
            item["last_run_timestamp"] = run["timestamp"]
    return sorted(grouped.values(), key=lambda item: item["last_run_timestamp"], reverse=True)
