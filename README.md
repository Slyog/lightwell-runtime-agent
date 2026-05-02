# Lightwell Runtime Agent

A local agent workspace that sends coding objectives to an external AI Execution Engine, observes real runtime results, classifies failures, and stores execution traces.

Lightwell is the control, UI, and observation layer. The AI Execution Engine is the runtime backend that generates code, executes it in Docker, repairs failures, and returns `trace_ids`, `stdout`, and `stderr`.

## What Lightwell Is

- A local UI and CLI for submitting coding objectives.
- An observation layer over the AI Execution Engine `/agent-runs` API.
- A JSONL-backed run history and inspection workspace.
- A simple experiment runner for repeatable behavior checks.
- A failure classifier that separates agent/code failures from infrastructure failures.

## What Lightwell Is Not

- It is not the runtime backend.
- It does not execute generated code itself.
- It does not replace Docker sandboxing.
- It is not a chatbot.
- It does not use a database or background job system.

## Relation To AI Execution Engine

```text
Lightwell Runtime Agent -> AI Execution Engine -> Docker Sandbox
```

Lightwell sends:

```json
{
  "objective": "task text",
  "max_attempts": 1
}
```

to the engine:

```text
POST /agent-runs
```

The engine returns execution truth: status, stdout, stderr, timeout state, and trace IDs. Lightwell records and displays those results.

## Routes

- `/`
  - Home workspace.
  - Shows the objective form, experiment selector, run controls, history link, and latest run summary.

- `/run`
  - Form submit endpoint.
  - Calls the existing runner/client logic and renders the run result.

- `/history`
  - JSONL-backed run overview.
  - Supports basic query filters:
    - `?status=success`
    - `?status=failure`
    - `?experiment=<name>`

- `/history/{run_id}`
  - Run inspection page.
  - Shows objective, metadata, trace IDs, per-attempt blocks, stdout, stderr, observations, and raw JSON.

- `/experiments`
  - Read-only experiment overview.
  - Groups historical runs by normalized experiment name.

- `/experiments/{experiment_name}`
  - Read-only experiment detail page.
  - Shows counts and runs for one experiment.

## JSONL Source Of Truth

JSONL logs are the persistent state for Lightwell.

- UI runs write logs named like `ui-<timestamp>-<experiment>.jsonl`.
- CLI runs can write any JSONL path using `--log`.
- History and experiment pages read JSONL directly.
- Malformed JSONL lines are skipped.
- Raw JSON is preserved and shown on run detail pages for debugging.

No database is used.

## Run Locally

Start the AI Execution Engine separately:

```powershell
cd C:\Users\slyse\Documents\ExecutionEngine\AIExecutionEngine
python -m uvicorn api:app --host 127.0.0.1 --port 8000
```

Start Lightwell:

```powershell
cd C:\Users\slyse\Documents\agentcoding
python -m uvicorn app:app --host 127.0.0.1 --port 8080
```

Open:

```text
http://127.0.0.1:8080
```

CLI usage still works:

```powershell
python runner.py "Write Python code that prints hello" --mode single --base-url http://127.0.0.1:8000
python runner.py --experiment missing_dependency --mode retry_heavy --base-url http://127.0.0.1:8000
python summarize.py agent_runs.jsonl
```

## Experiments

Predefined experiments live in `experiments/registry.py`.

- `missing_dependency`
- `file_assumption`
- `timeout`
- `standard_library_fallback`
- `syntax_simple`

Experiments are read-only named objectives. Lightwell does not provide experiment editing or creation UI.

## Failure Semantics

Successful runs display:

```text
failure_category = none
```

Failed runs preserve meaningful categories such as:

- `engine_unreachable`
- `docker_unavailable`
- `sandbox_start_failed`
- `timeout`
- `missing_dependency`
- `syntax_error`
- `runtime_error`
- `unknown_error`

Infrastructure failures are not counted as agent/code failures.

## Current Limitations

- Docker must be available for full sandbox execution.
- If Docker is unavailable, Lightwell still classifies it as an infrastructure failure.
- The UI is local-first and has no authentication.
- There is no database, pagination, charts, or background processing.
- Experiment and run detail pages are read-only.
