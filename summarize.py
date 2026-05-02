import argparse
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List


def load_events(path: Path) -> List[Dict[str, Any]]:
    events = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                events.append({"event": "invalid_json", "line_number": line_number, "raw": line})
                continue
            if isinstance(record, dict):
                events.append(record)
            else:
                events.append({"event": "invalid_json", "line_number": line_number, "raw": record})
    return events


def first_present(events: Iterable[Dict[str, Any]], key: str) -> Any:
    for event in events:
        value = event.get(key)
        if value not in (None, ""):
            return value
    return None


def last_present(events: Iterable[Dict[str, Any]], key: str) -> Any:
    value = None
    for event in events:
        candidate = event.get(key)
        if candidate not in (None, ""):
            value = candidate
    return value


def bool_or_unknown(value: Any) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    return "unknown"


def normalize_experiment_name(value: Any) -> str:
    text = "" if value is None else str(value).strip()
    if not text or text.lower() == "custom objective":
        return "custom"
    return text


def preview(value: Any, limit: int = 240) -> str:
    if value in (None, ""):
        return ""
    text = str(value).replace("\r", "\\r").replace("\n", "\\n")
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def classify_failure(stderr: str = "", timeout: bool = False, status: str = "") -> str:
    text = (stderr or "").lower()
    if "engine_unreachable" in text or "winerror 10061" in text or "connection refused" in text:
        return "engine_unreachable"
    if "dockerdesktoplinuxengine" in text or "dockerdesktopwindowsengine" in text:
        return "docker_unavailable"
    if "docker api" in text and ("daemon" in text or "pipe" in text or "connect" in text):
        return "docker_unavailable"
    if "cannot connect to the docker daemon" in text or "docker daemon is not running" in text:
        return "docker_unavailable"
    if "failed to start sandbox" in text or "sandbox start" in text or "container start" in text:
        return "sandbox_start_failed"
    if timeout or "timed out" in text or "timeout" in text:
        return "timeout"
    if "modulenotfounderror" in text or "no module named" in text:
        return "missing_dependency"
    if "syntaxerror" in text:
        return "syntax_error"
    if "traceback" in text or "exception" in text or "error" in text or (status == "failed" and text):
        return "runtime_error"
    return "unknown_error"


def collect_trace_ids(events: Iterable[Dict[str, Any]]) -> List[str]:
    trace_ids = []
    for event in events:
        values = event.get("trace_ids")
        if isinstance(values, list):
            candidates = values
        else:
            candidates = [event.get("trace_id")]
        for candidate in candidates:
            if candidate and candidate not in trace_ids:
                trace_ids.append(str(candidate))
    return trace_ids


def summarize(path: Path) -> Dict[str, Any]:
    events = load_events(path)
    final_event = next((event for event in reversed(events) if event.get("event") in {"observe", "failure"}), None)
    final_event = final_event or (events[-1] if events else {})
    trace_ids = collect_trace_ids(events)
    status = final_event.get("status")
    final_stderr = last_present(events, "stderr") or final_event.get("error") or ""
    failure_category = final_event.get("failure_category")

    if status == "completed":
        final_success = True
    elif status or failure_category or final_event.get("event") == "failure":
        final_success = False
    else:
        final_success = None

    summary = {
        "total_events": len(events),
        "experiment": normalize_experiment_name(first_present(events, "experiment")),
        "final_success": final_success,
        "failure_category": "none" if final_success is True else failure_category or classify_failure(final_stderr, bool(final_event.get("timeout")), str(status or "")),
        "is_agent_failure": final_event.get("is_agent_failure"),
        "is_infrastructure_failure": final_event.get("is_infrastructure_failure"),
        "trace_count": len(trace_ids),
        "final_stdout_preview": preview(last_present(events, "stdout")),
        "final_stderr_preview": preview(final_stderr),
    }
    if summary["is_agent_failure"] is None:
        summary["is_agent_failure"] = summary["failure_category"] in {
            "timeout",
            "missing_dependency",
            "syntax_error",
            "runtime_error",
        }
    if summary["is_infrastructure_failure"] is None:
        summary["is_infrastructure_failure"] = summary["failure_category"] in {
            "docker_unavailable",
            "sandbox_start_failed",
            "engine_unreachable",
        }
    return summary


def print_summary(summary: Dict[str, Any]) -> None:
    print(f"total_events: {summary['total_events']}")
    print(f"experiment: {summary['experiment']}")
    print(f"final_success: {bool_or_unknown(summary['final_success'])}")
    print(f"failure_category: {summary['failure_category']}")
    print(f"is_agent_failure: {bool_or_unknown(summary['is_agent_failure'])}")
    print(f"is_infrastructure_failure: {bool_or_unknown(summary['is_infrastructure_failure'])}")
    print(f"trace_count: {summary['trace_count']}")
    print(f"final_stdout_preview: {summary['final_stdout_preview']}")
    print(f"final_stderr_preview: {summary['final_stderr_preview']}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize a Co-Worker Light JSONL log.")
    parser.add_argument("jsonl_file")
    args = parser.parse_args()

    path = Path(args.jsonl_file)
    if not path.exists():
        parser.error(f"file not found: {path}")
    if not path.is_file():
        parser.error(f"not a file: {path}")

    print_summary(summarize(path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
