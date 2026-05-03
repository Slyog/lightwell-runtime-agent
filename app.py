import html
import json
from datetime import datetime, timezone
from pathlib import Path
from string import Template
from urllib.parse import parse_qs
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
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

app = FastAPI(title="Lightwell Playground")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def render_template(name: str, **context) -> HTMLResponse:
    template = Template((TEMPLATE_DIR / name).read_text(encoding="utf-8"))
    escaped = {key: str(value) for key, value in context.items()}
    return HTMLResponse(template.safe_substitute(escaped))


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
    return render_template("adaptive_run.html")


@app.post("/run", response_class=HTMLResponse)
async def run(request: Request):
    form = parse_form(await request.body())
    mode = form.get("mode") if form.get("mode") in {"single", "retry_heavy", "variation"} else "single"
    experiment = form.get("experiment") or None
    objective = form.get("objective", "").strip()
    max_attempts = clamp_max_attempts(form.get("max_attempts", "1"))
    base_url = form.get("base_url", "").strip() or "http://127.0.0.1:8000"

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
        )
        result["log_file"] = log_path.name

    return render_result(result, mode, max_attempts, base_url)


def render_result(result: dict, mode: str, max_attempts: int, base_url: str) -> HTMLResponse:
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
        task=esc(result.get("task", "")),
        experiment=esc(normalize_experiment_name(result.get("experiment"))),
        log_file=esc(result.get("log_file", "not written")),
        mode=esc(mode),
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
        attempt_blocks=render_attempt_blocks(run_item["attempt_blocks"]),
        raw_json=esc(raw_json),
    )
