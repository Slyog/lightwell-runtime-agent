# Lightwell Runtime Agent

A local agent workspace that sends coding objectives to an external AI Execution Engine, observes real runtime results, classifies failures, and stores execution traces.

## What Lightwell Is

Lightwell is the UI and CLI layer above the AI Execution Engine.

- Sends objectives to the engine's `POST /agent-runs` endpoint.
- Displays runtime results returned by the engine.
- Logs runs as JSONL.
- Supports run history, run inspection, predefined experiments, and experiment aggregation.
- Shows failure classification so infrastructure failures are visible separately from agent/code failures.

## What Lightwell Is Not

- Not the AI Execution Engine.
- Not a Docker runtime.
- Not a chatbot.
- Not a database-backed application.
- Not a simulation of execution results.

## Architecture

```text
User/UI -> Lightwell Runtime Agent -> AI Execution Engine -> Docker Runtime
```

Lightwell sends:

```json
{
  "objective": "task text",
  "max_attempts": 1
}
```

to:

```text
POST /agent-runs
```

The AI Execution Engine generates code, executes it in Docker, and returns execution results such as `stdout`, `stderr`, status, timeout state, and `trace_ids`.

Lightwell does not decide correctness by itself. It records and displays the runtime response from the engine.

## JSONL Source Of Truth

JSONL logs are the persistent state for Lightwell.

- UI runs write logs named like `ui-<timestamp>-<experiment>.jsonl`.
- CLI runs can write a JSONL path using `--log`.
- History and experiment pages read JSONL directly.
- Malformed JSONL lines are skipped.
- Raw JSON remains available on run detail pages for debugging.

Derived views:

- History
- Run detail
- Experiments
- Experiment detail

No database is used.

## Routes

- `/`
  - Home workspace.
  - Shows objective input, mode selection, experiment selection, run controls, and latest run summary.

- `/run`
  - Form submit endpoint.
  - Calls the existing runner/client logic and renders the run result.

- `/history`
  - JSONL-backed run overview.
  - Supports basic query filters for status and experiment.

- `/history/{run_id}`
  - Run inspection page.
  - Shows objective, metadata, trace IDs, attempts, stdout, stderr, observations, and raw JSON.

- `/experiments`
  - Read-only experiment overview.
  - Groups historical runs by normalized experiment name.

- `/experiments/{experiment_name}`
  - Read-only experiment detail page.
  - Shows counts and runs for one experiment.

## How To Run

Start the AI Execution Engine separately on port `8000`.

Local Windows example:

```powershell
cd C:\Users\slyse\Documents\ExecutionEngine\AIExecutionEngine
python -m uvicorn api:app --host 127.0.0.1 --port 8000
```

Codespaces engine example:

```bash
python -m uvicorn api:app --host 0.0.0.0 --port 8000
```

Start Lightwell on port `8080`.

Local Windows example:

```powershell
cd C:\Users\slyse\Documents\agentcoding
python -m uvicorn app:app --host 127.0.0.1 --port 8080
```

Codespaces Lightwell example:

```bash
python -m uvicorn app:app --host 0.0.0.0 --port 8080
```

Open:

```text
http://127.0.0.1:8080
```

CLI examples:

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

## Current Constraints

- No React.
- No database.
- JSONL only.
- Minimal local UI.
- No changes to the AI Execution Engine.
- No background jobs, charts, or authentication.

## Current Status

- Execution through the external AI Execution Engine works.
- JSONL logging works.
- Run history works.
- Run inspection works.
- Experiment aggregation works.
- Failure classification is visible in the CLI and UI.

## Current Limitations

- Docker must be available for full sandbox execution.
- If Docker is unavailable, Lightwell classifies it as an infrastructure failure.
- Experiment and run detail pages are read-only.
