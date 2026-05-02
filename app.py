import html
from datetime import datetime, timezone
from pathlib import Path
from string import Template
from urllib.parse import parse_qs

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from client import AgentRunClient
from experiments import EXPERIMENTS, get_experiment, list_experiments
from logger import JsonlLogger
from runner import run_task
from summarize import summarize


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
    tags = ['<option value="">custom objective</option>']
    tags.extend(option_tags(list_experiments(), selected).splitlines())
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


def jsonl_files():
    return sorted(LOG_DIR.glob("*.jsonl"), key=lambda path: path.stat().st_mtime, reverse=True)


@app.get("/", response_class=HTMLResponse)
def index():
    return render_template(
        "index.html",
        mode_options=option_tags(["single", "retry_heavy", "variation"], "single"),
        experiment_options=experiment_options(),
        objective="",
        max_attempts="1",
        base_url="http://127.0.0.1:8000",
    )


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
        failure_category=esc(result.get("failure_category")),
        is_agent_failure=esc(str(result.get("is_agent_failure")).lower()),
        is_infrastructure_failure=esc(str(result.get("is_infrastructure_failure")).lower()),
        stdout=esc(result.get("final_stdout")),
        stderr=esc(result.get("final_stderr")),
        trace_ids=trace_items or "<li>none</li>",
        observations=observation_items or "<li>none</li>",
        task=esc(result.get("task", "")),
        experiment=esc(result.get("experiment", "none")),
        log_file=esc(result.get("log_file", "not written")),
        mode=esc(mode),
        max_attempts=esc(max_attempts),
        base_url=esc(base_url),
    )


@app.get("/history", response_class=HTMLResponse)
def history():
    rows = []
    for path in jsonl_files():
        modified = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        rows.append(
            f'<tr><td><a href="/history/{esc(path.name)}">{esc(path.name)}</a></td>'
            f"<td>{path.stat().st_size}</td><td>{esc(modified)}</td></tr>"
        )
    return render_template("history.html", title="History", rows="\n".join(rows) or '<tr><td colspan="3">No JSONL logs found.</td></tr>', summary="")


@app.get("/history/{filename}", response_class=HTMLResponse)
def history_summary(filename: str):
    path = LOG_DIR / Path(filename).name
    if path.suffix != ".jsonl" or not path.exists() or not path.is_file():
        summary_html = "<p>Log file not found.</p>"
    else:
        data = summarize(path)
        summary_html = "".join(f"<dt>{esc(key)}</dt><dd>{esc(value)}</dd>" for key, value in data.items())

    rows = []
    for path_item in jsonl_files():
        rows.append(f'<tr><td><a href="/history/{esc(path_item.name)}">{esc(path_item.name)}</a></td><td>{path_item.stat().st_size}</td><td></td></tr>')
    return render_template("history.html", title=f"History: {esc(filename)}", rows="\n".join(rows), summary=f"<dl>{summary_html}</dl>")
