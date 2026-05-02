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


def summarize_run(path: Path) -> Dict[str, Any]:
    records = read_jsonl(path)
    final = next((record for record in reversed(records) if record.get("event") in {"observe", "failure"}), {})
    status = final.get("status")
    raw_failure_category = final.get("failure_category") or final.get("error_type") or "unknown"
    success = True if status == "completed" else False if final else None
    failure_category = "none" if success else raw_failure_category
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
