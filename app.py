import html
import hashlib
import json
import os
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from string import Template
from urllib.parse import parse_qs
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from client import AgentRunClient
from experiments import EXPERIMENTS, get_experiment, list_experiments as list_experiment_names
from history_reader import get_run, list_experiments, list_runs, normalize_experiment_name
from logger import JsonlLogger
from runner import run_task


BASE_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
LOG_DIR = BASE_DIR
DATA_DIR = BASE_DIR / "data"
ADAPTIVE_RUN_HISTORY_PATH = DATA_DIR / "adaptive_runs.jsonl"
DEFAULT_ADAPTIVE_EXECUTION_BASE_URL = "https://stunning-space-happiness-69j455w46v4247p7-8880.app.github.dev"

app = FastAPI(title="Lightwell Playground")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def render_template(name: str, **context) -> HTMLResponse:
    template = Template((TEMPLATE_DIR / name).read_text(encoding="utf-8"))
    escaped = {key: str(value) for key, value in context.items()}
    return HTMLResponse(template.safe_substitute(escaped))


def adaptive_execution_endpoint(value: str | None = None) -> str:
    base = (value or DEFAULT_ADAPTIVE_EXECUTION_BASE_URL).strip().rstrip("/")
    if base.endswith("/adaptive-execution/run"):
        return base
    return f"{base}/adaptive-execution/run"


def esc(value) -> str:
    return html.escape("" if value is None else str(value))


def option_tags(values, selected="") -> str:
    tags = []
    for value in values:
        mark = " selected" if value == selected else ""
        tags.append(f'<option value="{esc(value)}"{mark}>{esc(value)}</option>')
    return "\n".join(tags)


def experiment_options(selected="") -> str:
    tags = ['<option value="">custom</option>']
    tags.extend(option_tags(list_experiment_names(), selected).splitlines())
    return "\n".join(tags)


def parse_form(body: bytes) -> dict:
    parsed = parse_qs(body.decode("utf-8"), keep_blank_values=True)
    return {key: values[-1] if values else "" for key, values in parsed.items()}


def clamp_max_attempts(value: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 1
    return max(1, min(5, number))


def final_adaptive_attempt(attempts) -> dict:
    return attempts[-1] if isinstance(attempts, list) and attempts else {}


def latest_attempt_value(attempts, key: str):
    if not isinstance(attempts, list):
        return None
    for attempt in reversed(attempts):
        if isinstance(attempt, dict) and attempt.get(key):
            return attempt.get(key)
    return None


def first_attempt_value(attempts, key: str):
    if not isinstance(attempts, list):
        return None
    for attempt in attempts:
        if isinstance(attempt, dict) and attempt.get(key):
            return attempt.get(key)
    return None


def truthy_result(value) -> bool:
    return value is True or str(value).lower() == "true"


def truthy_form_value(value: str) -> bool:
    return str(value or "").lower() in {"1", "true", "yes", "on"}


def append_adaptive_run_history(entry: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with ADAPTIVE_RUN_HISTORY_PATH.open("a", encoding="utf-8") as history_file:
        history_file.write(json.dumps(entry, ensure_ascii=False) + "\n")


def legacy_adaptive_run_id(line: str, index: int) -> str:
    digest = hashlib.sha256(f"{index}:{line}".encode("utf-8")).hexdigest()[:16]
    return f"legacy-{digest}"


def read_adaptive_run_history() -> list[dict]:
    if not ADAPTIVE_RUN_HISTORY_PATH.exists():
        return []

    runs = []
    for index, line in enumerate(ADAPTIVE_RUN_HISTORY_PATH.read_text(encoding="utf-8").splitlines()):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            item["_view_run_id"] = str(item.get("run_id") or legacy_adaptive_run_id(line, index))
            runs.append(item)
    return list(reversed(runs))


def get_adaptive_run(run_id: str) -> dict | None:
    for run_item in read_adaptive_run_history():
        if run_item.get("_view_run_id") == run_id or run_item.get("run_id") == run_id:
            return run_item
    return None


def render_adaptive_run_history_rows(runs: list[dict]) -> str:
    rows = []
    for run_item in runs:
        attempts = run_item.get("attempts") if isinstance(run_item.get("attempts"), list) else []
        success = "success" if run_item.get("success") is True else "failure"
        run_id = run_item.get("_view_run_id") or run_item.get("run_id") or ""
        rows.append(
            f"<tr><td>{esc(run_item.get('timestamp'))}</td>"
            f"<td><strong>{esc(run_item.get('method'))}</strong> {esc(run_item.get('endpoint_url'))}</td>"
            f"<td>{esc(success)}</td>"
            f"<td>{esc(len(attempts))}</td>"
            f"<td>{esc(run_item.get('final_error_type') or 'none')}</td>"
            f"<td>{esc(run_item.get('final_strategy') or 'none')}</td>"
            f'<td><a href="/adaptive-runs/history/{esc(run_id)}">detail</a></td></tr>'
        )
    return "\n".join(rows) or '<tr><td colspan="7">No adaptive runs recorded yet.</td></tr>'


def filter_adaptive_runs(runs: list[dict], q: str = "", method: str = "", status: str = "", error_type: str = "") -> list[dict]:
    q = q.strip().lower()
    method = method.strip().upper()
    status = status.strip().lower()
    error_type = error_type.strip()
    filtered = []

    for run_item in runs:
        endpoint_url = str(run_item.get("endpoint_url") or "")
        run_method = str(run_item.get("method") or "").upper()
        run_success = run_item.get("success") is True
        run_error_type = str(run_item.get("final_error_type") or "")

        if q and q not in endpoint_url.lower():
            continue
        if method and run_method != method:
            continue
        if status == "success" and not run_success:
            continue
        if status == "failure" and run_success:
            continue
        if error_type and run_error_type != error_type:
            continue
        filtered.append(run_item)

    return filtered


def adaptive_method_options(selected: str = "") -> str:
    selected = selected.upper()
    labels = [("", "all"), ("GET", "GET"), ("POST", "POST")]
    return "\n".join(
        f'<option value="{esc(value)}"{" selected" if value == selected else ""}>{esc(label)}</option>'
        for value, label in labels
    )


def adaptive_status_options(selected: str = "") -> str:
    labels = [("", "all"), ("success", "success"), ("failure", "failure")]
    return "\n".join(
        f'<option value="{esc(value)}"{" selected" if value == selected else ""}>{esc(label)}</option>'
        for value, label in labels
    )


def adaptive_error_type_options(runs: list[dict], selected: str = "") -> str:
    values = sorted({str(run.get("final_error_type") or "") for run in runs if run.get("final_error_type")})
    options = ['<option value="">all</option>']
    options.extend(option_tags(values, selected).splitlines())
    return "\n".join(options)


def adaptive_attempt_number(attempt: dict, index: int):
    return attempt.get("attempt_number") or attempt.get("attempt") or index + 1


def adaptive_attempt_label(attempt: dict, index: int, total: int) -> str:
    success = truthy_result(attempt.get("success")) or attempt.get("exit_code") == 0
    error_type = str(attempt.get("error_type") or "")
    if total > 1 and index > 0:
        return "repaired handling" if success else "repair failed"
    if error_type.startswith("HTTP"):
        return "API response failure"
    return "success" if success else "failed"


def render_adaptive_attempt_timeline(attempts) -> str:
    if not isinstance(attempts, list) or not attempts:
        return "<p>No attempts recorded.</p>"

    blocks = []
    for index, attempt in enumerate(attempts):
        if not isinstance(attempt, dict):
            continue
        success = truthy_result(attempt.get("success")) or attempt.get("exit_code") == 0
        label = adaptive_attempt_label(attempt, index, len(attempts))
        number = adaptive_attempt_number(attempt, index)
        error_type = attempt.get("error_type") or "none"
        strategy = attempt.get("strategy") or "none"
        blocks.append(
            f'<article class="adaptive-attempt {"success" if success else "failure"}">'
            f'<div class="attempt-marker">{esc(number)}</div>'
            '<div class="attempt-body">'
            '<div class="attempt-heading">'
            f"<h3>Attempt {esc(number)} ({esc(label)})</h3>"
            '<div class="attempt-badges">'
            f'<span class="badge badge-label">{esc(label)}</span>'
            f'<span class="badge badge-error">{esc(error_type)}</span>'
            f'<span class="badge badge-strategy">{esc(strategy)}</span>'
            "</div></div>"
            "<h4>code</h4>"
            f"<pre><code>{esc(attempt.get('code'))}</code></pre>"
            '<div class="result-grid">'
            f"<div><span>exit_code</span><strong>{esc(attempt.get('exit_code'))}</strong></div>"
            f'<div><span>error_type</span><strong><span class="badge badge-error">{esc(error_type)}</span></strong></div>'
            f'<div><span>strategy</span><strong><span class="badge badge-strategy">{esc(strategy)}</span></strong></div>'
            f'<div class="wide output-block"><span>stdout</span><pre>{esc(attempt.get("stdout"))}</pre></div>'
            f'<div class="wide output-block"><span>stderr</span><pre>{esc(attempt.get("stderr"))}</pre></div>'
            "</div></div></article>"
        )
    return "\n".join(blocks) or "<p>No attempts recorded.</p>"


def adaptive_run_summary(run_item: dict) -> dict:
    attempts = run_item.get("attempts") if isinstance(run_item.get("attempts"), list) else []
    success = run_item.get("success") is True
    return {
        "attempts": attempts,
        "success": success,
        "success_label": "success" if success else "failure",
        "total_attempts": len(attempts),
        "first_error_type": first_attempt_value(attempts, "error_type") or "none",
        "final_error_type": run_item.get("final_error_type") or latest_attempt_value(attempts, "error_type") or "none",
        "final_strategy": run_item.get("final_strategy") or latest_attempt_value(attempts, "strategy") or "none",
        "repaired": len(attempts) > 1 and success,
    }


def markdown_block(value) -> str:
    text = "" if value is None else str(value)
    return f"```text\n{text}\n```"


def render_adaptive_run_markdown(run_item: dict, run_id: str) -> str:
    summary = adaptive_run_summary(run_item)
    lines = [
        "# Adaptive Run Debug Report",
        "",
        f"- run_id: {run_id}",
        f"- timestamp: {run_item.get('timestamp') or ''}",
        f"- endpoint_url: {run_item.get('endpoint_url') or ''}",
        f"- method: {run_item.get('method') or ''}",
        f"- final outcome: {summary['success_label']}",
        f"- total attempts: {summary['total_attempts']}",
        f"- first_error_type: {summary['first_error_type']}",
        f"- final_error_type: {summary['final_error_type']}",
        f"- final_strategy: {summary['final_strategy']}",
        f"- repaired: {'yes' if summary['repaired'] else 'no'}",
        "",
        "## Generated Objective",
        "",
        markdown_block(run_item.get("objective")),
        "",
        "## Attempts",
        "",
    ]

    if not summary["attempts"]:
        lines.append("No attempts recorded.")
        return "\n".join(lines).rstrip() + "\n"

    for index, attempt in enumerate(summary["attempts"]):
        if not isinstance(attempt, dict):
            continue
        number = adaptive_attempt_number(attempt, index)
        lines.extend(
            [
                f"### Attempt {number}",
                "",
                f"- error_type: {attempt.get('error_type') or 'none'}",
                f"- strategy: {attempt.get('strategy') or 'none'}",
                f"- exit_code: {attempt.get('exit_code')}",
                "",
                "#### Code",
                "",
                "```python",
                "" if attempt.get("code") is None else str(attempt.get("code")),
                "```",
                "",
                "#### stdout",
                "",
                markdown_block(attempt.get("stdout")),
                "",
                "#### stderr",
                "",
                markdown_block(attempt.get("stderr")),
                "",
            ]
        )

    return "\n".join(lines).rstrip() + "\n"


def percent(part: int, total: int) -> str:
    if total <= 0:
        return "0.0%"
    return f"{(part / total) * 100:.1f}%"


def table_rows(rows: list[tuple]) -> str:
    if not rows:
        return '<tr><td colspan="4">No data.</td></tr>'
    return "\n".join("<tr>" + "".join(f"<td>{esc(cell)}</td>" for cell in row) + "</tr>" for row in rows)


def adaptive_run_error_type(run_item: dict) -> str:
    attempts = run_item.get("attempts") if isinstance(run_item.get("attempts"), list) else []
    return str(run_item.get("final_error_type") or latest_attempt_value(attempts, "error_type") or "none")


def adaptive_run_strategy(run_item: dict) -> str:
    attempts = run_item.get("attempts") if isinstance(run_item.get("attempts"), list) else []
    return str(run_item.get("final_strategy") or latest_attempt_value(attempts, "strategy") or "none")


def adaptive_run_attempt_count(run_item: dict) -> int:
    attempts = run_item.get("attempts")
    return len(attempts) if isinstance(attempts, list) else 0


def attempt_strategy(attempt: dict) -> str:
    return str(attempt.get("strategy") or "none")


def build_adaptive_insights(runs: list[dict]) -> dict:
    total_runs = len(runs)
    success_count = sum(1 for run in runs if run.get("success") is True)
    attempt_total = sum(adaptive_run_attempt_count(run) for run in runs)

    error_counts = Counter()
    strategy_counts = Counter()
    strategy_effectiveness = {}
    error_success = {}
    failing_endpoints = Counter()

    for run in runs:
        attempts = run.get("attempts") if isinstance(run.get("attempts"), list) else []
        attempt_count = len(attempts)
        error_type = adaptive_run_error_type(run)
        strategy = adaptive_run_strategy(run)
        success = run.get("success") is True
        endpoint_url = str(run.get("endpoint_url") or "unknown")

        if error_type != "none":
            error_counts[error_type] += 1
            bucket = error_success.setdefault(error_type, {"total": 0, "success": 0})
            bucket["total"] += 1
            if success:
                bucket["success"] += 1

        if strategy != "none":
            strategy_counts[strategy] += 1

        for attempt in attempts:
            if not isinstance(attempt, dict):
                continue
            used_strategy = attempt_strategy(attempt)
            if used_strategy == "none":
                continue
            stats = strategy_effectiveness.setdefault(
                used_strategy,
                {"usage": 0, "final_success": 0, "attempt_total": 0},
            )
            stats["usage"] += 1
            stats["attempt_total"] += attempt_count
            if success:
                stats["final_success"] += 1

        if not success:
            failing_endpoints[endpoint_url] += 1

    success_rows = [
        ("total runs", total_runs),
        ("successful runs", success_count),
        ("failed runs", total_runs - success_count),
        ("success rate", percent(success_count, total_runs)),
        ("average attempts per run", f"{attempt_total / total_runs:.2f}" if total_runs else "0.00"),
    ]

    error_rows = [(error_type, count) for error_type, count in error_counts.most_common()]
    strategy_rows = [(strategy, count) for strategy, count in strategy_counts.most_common()]
    strategy_effectiveness_rows = [
        (
            strategy,
            values["usage"],
            values["final_success"],
            percent(values["final_success"], values["usage"]),
            f"{values['attempt_total'] / values['usage']:.2f}" if values["usage"] else "0.00",
        )
        for strategy, values in sorted(
            strategy_effectiveness.items(),
            key=lambda item: (-item[1]["final_success"], -item[1]["usage"], item[0]),
        )
    ]
    error_success_rows = [
        (error_type, values["success"], values["total"], percent(values["success"], values["total"]))
        for error_type, values in sorted(error_success.items())
    ]
    endpoint_rows = [(endpoint_url, count) for endpoint_url, count in failing_endpoints.most_common()]

    return {
        "success_rows": table_rows(success_rows),
        "error_rows": table_rows(error_rows),
        "strategy_rows": table_rows(strategy_rows),
        "strategy_effectiveness_rows": table_rows(strategy_effectiveness_rows),
        "error_success_rows": table_rows(error_success_rows),
        "endpoint_rows": table_rows(endpoint_rows),
    }


def filter_runs(runs, status: str = "", experiment: str = ""):
    filtered = []
    for run_item in runs:
        if status == "success" and run_item["success"] is not True:
            continue
        if status == "failure" and run_item["success"] is not False:
            continue
        if experiment and run_item.get("experiment") != experiment:
            continue
        filtered.append(run_item)
    return filtered


def status_options(selected: str) -> str:
    labels = [("", "all"), ("success", "success"), ("failure", "failure")]
    return "\n".join(
        f'<option value="{esc(value)}"{" selected" if value == selected else ""}>{esc(label)}</option>'
        for value, label in labels
    )


def display_failure_category(success, failure_category) -> str:
    return "none" if success is True else str(failure_category or "unknown")


def api_signal_value(signals: dict, key: str, fallback="none") -> str:
    value = signals.get(key)
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    if value is None:
        return str(fallback)
    return str(value).lower() if isinstance(value, bool) else str(value)


def latest_adaptive_api_signals(run_item: dict) -> dict | None:
    signals = run_item.get("api_signals")
    if isinstance(signals, dict):
        return signals
    attempts = run_item.get("attempts") if isinstance(run_item.get("attempts"), list) else []
    for attempt in reversed(attempts):
        if isinstance(attempt, dict) and isinstance(attempt.get("api_signals"), dict):
            return attempt["api_signals"]
    events = run_item.get("events") if isinstance(run_item.get("events"), list) else []
    for event in reversed(events):
        if isinstance(event, dict) and isinstance(event.get("api_signals"), dict):
            return event["api_signals"]
    return None


def render_api_signals(result: dict) -> str:
    signals = result.get("api_signals")
    if not isinstance(signals, dict):
        return ""

    final_success = signals.get("final_success", result.get("success"))
    rows = [
        ("status_sequence", api_signal_value(signals, "status_sequence", "[]")),
        ("network_reachable", api_signal_value(signals, "network_reachable")),
        ("auth_failure_observed", api_signal_value(signals, "auth_failure_observed")),
        ("validation_failure_observed", api_signal_value(signals, "validation_failure_observed")),
        ("success_observed", api_signal_value(signals, "success_observed")),
        ("final_success", api_signal_value({"final_success": final_success}, "final_success")),
        ("failure_category", api_signal_value(signals, "failure_category")),
    ]
    rows_html = "\n".join(
        f"<div><span>{esc(label)}</span><strong>{esc(value)}</strong></div>"
        for label, value in rows
    )
    return f'<section class="panel result-grid"><h2 class="wide">API Signals</h2>{rows_html}</section>'


def event_attempt(attempts: list[dict], attempt_number) -> dict:
    for attempt in attempts:
        if isinstance(attempt, dict) and str(adaptive_attempt_number(attempt, -1)) == str(attempt_number):
            return attempt
    return {}


def generated_action_summary(event: dict, attempt: dict) -> str:
    code = str(event.get("code") or attempt.get("code") or "")
    parts = []
    if "host.docker.internal" in code:
        parts.append("target host.docker.internal")
    if "Authorization" in code:
        parts.append("with Authorization header")
    else:
        parts.append("without Authorization header")
    if '"age": 25' in code or "'age': 25" in code:
        parts.append("valid payload types")
    elif '"age": \'25\'' in code or "'age': '25'" in code or '"age": "25"' in code:
        parts.append("invalid age type")
    return ", ".join(parts) if parts else "generated request"


def decision_next_attempt_strategy(event: dict) -> str:
    value = event.get("next_attempt_strategy")
    if value:
        return str(value)
    action = str(event.get("action") or "")
    labels = {
        "add_authorization_header": "retry with Authorization header",
        "fix_payload_types": "retry with corrected payload types",
        "switch_target_to_host_docker_internal": "retry against host.docker.internal",
        "stop": "stop retrying",
    }
    return labels.get(action, action or "none")


def render_adaptive_event_timeline(run_item: dict) -> str:
    events = run_item.get("events") if isinstance(run_item.get("events"), list) else []
    attempts = run_item.get("attempts") if isinstance(run_item.get("attempts"), list) else []
    if not events:
        return "<p>No adaptive event chain recorded for this run.</p>"

    blocks = []
    for index, event in enumerate(events, start=1):
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("event") or "event")
        attempt_number = event.get("attempt_number") or event.get("attempt") or ""
        attempt = event_attempt(attempts, attempt_number)
        title_attempt = f"Attempt {attempt_number}" if attempt_number != "" else f"Event {index}"

        if event_type == "execute":
            rows = [
                ("event", "execute"),
                ("strategy", attempt.get("strategy") or event.get("strategy") or "deterministic_request"),
                ("generated action", generated_action_summary(event, attempt)),
            ]
        elif event_type == "observe":
            signals = event.get("api_signals") if isinstance(event.get("api_signals"), dict) else {}
            rows = [
                ("event", "observe"),
                ("status_sequence", api_signal_value(signals, "status_sequence", "[]")),
                ("failure_category", api_signal_value(signals, "failure_category")),
                ("final_success", api_signal_value(signals, "final_success")),
            ]
        elif event_type == "decision":
            rows = [
                ("event", "decision"),
                ("reason", event.get("reason") or "none"),
                ("action", event.get("action") or "none"),
                ("next_attempt_strategy", decision_next_attempt_strategy(event)),
            ]
        else:
            rows = [("event", event_type)]

        rows_html = "".join(
            f"<div><span>{esc(label)}</span><strong>{esc(value)}</strong></div>"
            for label, value in rows
        )
        blocks.append(
            f'<article class="adaptive-event adaptive-event-{esc(event_type)}">'
            f'<div class="attempt-marker">{esc(attempt_number or index)}</div>'
            '<div class="attempt-body">'
            f"<h3>{esc(title_attempt)} {esc(event_type)}</h3>"
            f'<div class="result-grid">{rows_html}</div>'
            "</div></article>"
        )
    return "\n".join(blocks) or "<p>No adaptive event chain recorded for this run.</p>"


def latest_run_summary() -> str:
    runs = list_runs(LOG_DIR)
    if not runs:
        return "<p>No runs recorded yet.</p>"
    latest = runs[0]
    success = "unknown" if latest["success"] is None else str(latest["success"]).lower()
    return (
        '<div class="latest-grid">'
        f"<div><span>timestamp</span><strong>{esc(latest['timestamp'])}</strong></div>"
        f"<div><span>success</span><strong>{esc(success)}</strong></div>"
        f"<div><span>failure_category</span><strong>{esc(display_failure_category(latest['success'], latest['failure_category']))}</strong></div>"
        f"<div><span>trace_count</span><strong>{esc(latest['trace_count'])}</strong></div>"
        f"<div><span>experiment</span><strong>{esc(latest['experiment'])}</strong></div>"
        f"<div class=\"wide\"><span>objective</span><strong>{esc(latest['objective'])}</strong></div>"
        f'<div><a href="/history/{esc(latest["run_id"])}">Open detail</a></div>'
        "</div>"
    )


def render_attempt_blocks(attempts) -> str:
    if not attempts:
        return "<p>No attempt records found.</p>"
    blocks = []
    for attempt in attempts:
        success = "unknown" if attempt["success"] is None else str(attempt["success"]).lower()
        blocks.append(
            '<section class="attempt-block">'
            f"<h3>Attempt {esc(attempt['attempt'])}</h3>"
            '<div class="result-grid">'
            f"<div><span>success</span><strong>{esc(success)}</strong></div>"
            f"<div><span>failure_category</span><strong>{esc(attempt['failure_category'])}</strong></div>"
            f"<div><span>trace_id</span><strong>{esc(attempt['trace_id'])}</strong></div>"
            "</div>"
            "<h4>stdout</h4>"
            f"<pre>{esc(attempt['stdout'])}</pre>"
            "<h4>stderr</h4>"
            f"<pre>{esc(attempt['stderr'])}</pre>"
            "<h4>observation</h4>"
            f"<pre>{esc(attempt['observation'])}</pre>"
            "</section>"
        )
    return "\n".join(blocks)


@app.get("/", response_class=HTMLResponse)
def index():
    return render_template(
        "index.html",
        mode_options=option_tags(["single", "retry_heavy", "variation"], "single"),
        experiment_options=experiment_options(),
        objective="",
        max_attempts="1",
        base_url="http://127.0.0.1:8000",
        latest_run=latest_run_summary(),
    )


@app.get("/adaptive-run", response_class=HTMLResponse)
def adaptive_run():
    endpoint = adaptive_execution_endpoint(os.environ.get("ADAPTIVE_EXECUTION_API_URL"))
    return render_template("adaptive_run.html", adaptive_execution_api_url=json.dumps(endpoint))


@app.post("/adaptive-runs/history")
async def save_adaptive_run_history(request: Request):
    payload = await request.json()
    result = payload.get("result") if isinstance(payload.get("result"), dict) else payload
    attempts = result.get("attempts") if isinstance(result.get("attempts"), list) else []
    events = result.get("events") if isinstance(result.get("events"), list) else []
    final_attempt = result.get("final_attempt") if isinstance(result.get("final_attempt"), dict) else final_adaptive_attempt(attempts)

    entry = {
        "run_id": uuid.uuid4().hex,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "endpoint_url": str(payload.get("endpoint_url") or ""),
        "method": str(payload.get("method") or ""),
        "json_body": str(payload.get("json_body") or ""),
        "objective": str(payload.get("objective") or result.get("objective") or ""),
        "success": truthy_result(result.get("success")),
        "attempts": attempts,
        "events": events,
        "api_signals": result.get("api_signals") if isinstance(result.get("api_signals"), dict) else latest_adaptive_api_signals({"attempts": attempts, "events": events}),
        "final_error_type": final_attempt.get("error_type") or latest_attempt_value(attempts, "error_type"),
        "final_strategy": final_attempt.get("strategy") or latest_attempt_value(attempts, "strategy"),
    }
    append_adaptive_run_history(entry)
    return JSONResponse({"saved": True})


@app.get("/adaptive-runs/history", response_class=HTMLResponse)
def adaptive_runs_history(request: Request):
    q = request.query_params.get("q", "").strip()
    method = request.query_params.get("method", "").strip().upper()
    status = request.query_params.get("status", "").strip().lower()
    error_type = request.query_params.get("error_type", "").strip()
    if method not in {"", "GET", "POST"}:
        method = ""
    if status not in {"", "success", "failure"}:
        status = ""

    runs = read_adaptive_run_history()
    filtered_runs = filter_adaptive_runs(runs, q, method, status, error_type)
    return render_template(
        "adaptive_runs_history.html",
        q=esc(q),
        method_options=adaptive_method_options(method),
        status_options=adaptive_status_options(status),
        error_type_options=adaptive_error_type_options(runs, error_type),
        rows=render_adaptive_run_history_rows(filtered_runs),
    )


@app.get("/adaptive-runs/history/{run_id}", response_class=HTMLResponse)
def adaptive_run_detail(run_id: str):
    run_item = get_adaptive_run(run_id)
    if run_item is None:
        return HTMLResponse("Adaptive run not found", status_code=404)

    view_run_id = run_item.get("_view_run_id") or run_item.get("run_id") or run_id
    summary = adaptive_run_summary(run_item)
    api_signals = latest_adaptive_api_signals(run_item)
    run_item_with_signals = {**run_item, "api_signals": api_signals} if isinstance(api_signals, dict) else run_item

    return render_template(
        "adaptive_run_detail.html",
        run_id=esc(view_run_id),
        markdown_url=f"/adaptive-runs/history/{esc(view_run_id)}/markdown",
        timestamp=esc(run_item.get("timestamp")),
        endpoint_url=esc(run_item.get("endpoint_url")),
        method=esc(run_item.get("method")),
        json_body=esc(run_item.get("json_body")),
        objective=esc(run_item.get("objective")),
        success=esc(summary["success_label"]),
        total_attempts=esc(summary["total_attempts"]),
        first_error_type=esc(summary["first_error_type"]),
        final_error_type=esc(summary["final_error_type"]),
        final_strategy=esc(summary["final_strategy"]),
        repaired=esc("yes" if summary["repaired"] else "no"),
        api_signals_section=render_api_signals(run_item_with_signals),
        event_timeline=render_adaptive_event_timeline(run_item),
        attempts=render_adaptive_attempt_timeline(summary["attempts"]),
    )


@app.get("/adaptive-runs/history/{run_id}/markdown", response_class=PlainTextResponse)
def adaptive_run_markdown(run_id: str):
    run_item = get_adaptive_run(run_id)
    if run_item is None:
        return PlainTextResponse("Adaptive run not found", status_code=404)

    view_run_id = run_item.get("_view_run_id") or run_item.get("run_id") or run_id
    return PlainTextResponse(
        render_adaptive_run_markdown(run_item, view_run_id),
        media_type="text/markdown; charset=utf-8",
    )


@app.get("/adaptive-runs/insights", response_class=HTMLResponse)
def adaptive_run_insights():
    insights = build_adaptive_insights(read_adaptive_run_history())
    return render_template(
        "adaptive_run_insights.html",
        success_rows=insights["success_rows"],
        error_rows=insights["error_rows"],
        strategy_rows=insights["strategy_rows"],
        strategy_effectiveness_rows=insights["strategy_effectiveness_rows"],
        error_success_rows=insights["error_success_rows"],
        endpoint_rows=insights["endpoint_rows"],
    )


@app.post("/run", response_class=HTMLResponse)
async def run(request: Request):
    form = parse_form(await request.body())
    mode = form.get("mode") if form.get("mode") in {"single", "retry_heavy", "variation"} else "single"
    experiment = form.get("experiment") or None
    objective = form.get("objective", "").strip()
    max_attempts = clamp_max_attempts(form.get("max_attempts", "1"))
    base_url = form.get("base_url", "").strip() or "http://127.0.0.1:8000"
    allow_network = truthy_form_value(form.get("allow_network", ""))

    if experiment:
        objective = get_experiment(experiment)
    if not objective:
        result = {
            "success": False,
            "failure_category": "unknown_error",
            "is_agent_failure": False,
            "is_infrastructure_failure": False,
            "final_stdout": "",
            "final_stderr": "objective is required",
            "trace_ids": [],
            "observations": ["No objective or experiment was provided."],
        }
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        suffix = experiment or "custom"
        log_path = LOG_DIR / f"ui-{stamp}-{suffix}.jsonl"
        result = run_task(
            objective,
            mode,
            AgentRunClient(base_url),
            JsonlLogger(str(log_path)),
            experiment,
            max_attempts_override=max_attempts,
            allow_network=allow_network,
        )
        result["log_file"] = log_path.name

    return render_result(result, mode, max_attempts, base_url, allow_network)


def render_result(result: dict, mode: str, max_attempts: int, base_url: str, allow_network: bool = False) -> HTMLResponse:
    trace_items = "\n".join(f"<li><code>{esc(trace_id)}</code></li>" for trace_id in result.get("trace_ids", []))
    observation_items = "\n".join(f"<li>{esc(item)}</li>" for item in result.get("observations", []))
    return render_template(
        "result.html",
        success=esc(str(result.get("success")).lower()),
        failure_category=esc(display_failure_category(result.get("success"), result.get("failure_category"))),
        is_agent_failure=esc(str(result.get("is_agent_failure")).lower()),
        is_infrastructure_failure=esc(str(result.get("is_infrastructure_failure")).lower()),
        stdout=esc(result.get("final_stdout")),
        stderr=esc(result.get("final_stderr")),
        trace_ids=trace_items or "<li>none</li>",
        observations=observation_items or "<li>none</li>",
        api_signals_section=render_api_signals(result),
        task=esc(result.get("task", "")),
        experiment=esc(normalize_experiment_name(result.get("experiment"))),
        log_file=esc(result.get("log_file", "not written")),
        mode=esc(mode),
        network=esc("enabled" if allow_network else "none"),
        max_attempts=esc(max_attempts),
        base_url=esc(base_url),
    )


@app.get("/history", response_class=HTMLResponse)
def history(request: Request):
    status_filter = request.query_params.get("status", "")
    if status_filter not in {"", "success", "failure"}:
        status_filter = ""
    raw_experiment_filter = request.query_params.get("experiment", "").strip()
    experiment_filter = normalize_experiment_name(raw_experiment_filter) if raw_experiment_filter else ""
    rows = []
    for run_item in filter_runs(list_runs(LOG_DIR), status_filter, experiment_filter):
        success = "unknown" if run_item["success"] is None else str(run_item["success"]).lower()
        rows.append(
            f"<tr><td>{esc(run_item['timestamp'])}</td>"
            f"<td>{esc(run_item['objective'])}</td>"
            f"<td>{esc(success)}</td>"
            f"<td>{esc(display_failure_category(run_item['success'], run_item['failure_category']))}</td>"
            f"<td>{esc(run_item['trace_count'])}</td>"
            f"<td>{esc(run_item['experiment'] or '')}</td>"
            f'<td><a href="/history/{esc(run_item["run_id"])}">detail</a></td></tr>'
        )
    return render_template(
        "history.html",
        title="History",
        rows="\n".join(rows) or '<tr><td colspan="7">No runs match these filters.</td></tr>',
        status_options=status_options(status_filter),
        experiment_filter=esc(experiment_filter),
    )


@app.get("/experiments", response_class=HTMLResponse)
def experiments_overview():
    rows = []
    for experiment in list_experiments(LOG_DIR):
        rows.append(
            f'<tr><td><a href="/experiments/{quote(experiment["name"])}">{esc(experiment["name"])}</a></td>'
            f"<td>{esc(experiment['run_count'])}</td>"
            f"<td>{esc(experiment['success_count'])}</td>"
            f"<td>{esc(experiment['failure_count'])}</td>"
            f"<td>{esc(experiment['last_run_timestamp'])}</td>"
            f'<td><a href="/history?experiment={quote(experiment["name"])}">history</a></td></tr>'
        )
    return render_template(
        "experiments.html",
        rows="\n".join(rows) or '<tr><td colspan="6">No experiment runs found.</td></tr>',
    )


@app.get("/experiments/{experiment_name}", response_class=HTMLResponse)
def experiment_detail(experiment_name: str):
    experiment_name = normalize_experiment_name(experiment_name)
    runs = [run for run in list_runs(LOG_DIR) if run["experiment"] == experiment_name]
    if not runs:
        return HTMLResponse("Experiment not found", status_code=404)

    success_count = sum(1 for run in runs if run["success"] is True)
    failure_count = sum(1 for run in runs if run["success"] is False)
    last_run_timestamp = max((run["timestamp"] for run in runs if run["timestamp"]), default="")
    rows = []
    for run_item in runs:
        success = "unknown" if run_item["success"] is None else str(run_item["success"]).lower()
        rows.append(
            f"<tr><td>{esc(run_item['timestamp'])}</td>"
            f"<td>{esc(run_item['objective'])}</td>"
            f"<td>{esc(success)}</td>"
            f"<td>{esc(display_failure_category(run_item['success'], run_item['failure_category']))}</td>"
            f"<td>{esc(run_item['trace_count'])}</td>"
            f'<td><a href="/history/{esc(run_item["run_id"])}">detail</a></td></tr>'
        )

    return render_template(
        "experiment_detail.html",
        experiment_name=esc(experiment_name),
        run_count=esc(len(runs)),
        success_count=esc(success_count),
        failure_count=esc(failure_count),
        last_run_timestamp=esc(last_run_timestamp),
        filtered_history_url=f"/history?experiment={quote(experiment_name)}",
        rows="\n".join(rows),
    )


@app.get("/history/{run_id}", response_class=HTMLResponse)
def run_detail(run_id: str):
    run_item = get_run(LOG_DIR, run_id)
    if run_item is None:
        return HTMLResponse("Run not found", status_code=404)

    trace_ids = "\n".join(run_item["trace_ids"])
    raw_json = json.dumps(run_item["raw_records"], indent=2, sort_keys=True)
    success = "unknown" if run_item["success"] is None else str(run_item["success"]).lower()

    return render_template(
        "run_detail.html",
        run_id=esc(run_item["run_id"]),
        objective=esc(run_item["objective"]),
        timestamp=esc(run_item["timestamp"]),
        experiment=esc(run_item["experiment"] or ""),
        success=esc(success),
        failure_category=esc(display_failure_category(run_item["success"], run_item["failure_category"])),
        attempts=esc(run_item["attempts"]),
        trace_ids=esc(trace_ids),
        api_signals_section=render_api_signals(run_item),
        attempt_blocks=render_attempt_blocks(run_item["attempt_blocks"]),
        raw_json=esc(raw_json),
    )
