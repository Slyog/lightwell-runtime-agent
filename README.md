# Lightwell Runtime Agent

Lightwell is a local API Integration Debug Journal built on adaptive execution.

It provides a focused UI for running API integration checks, observing real runtime output, tracking repair attempts, and saving the result as local JSONL history. Lightwell observes real execution results. The LLM proposes code; the runtime determines truth.

## What Lightwell Is

Lightwell is the observation and journal layer for adaptive API debugging.

- It turns structured API inputs into an adaptive-execution objective.
- It calls the adaptive-execution service and displays every attempt.
- It records adaptive runs as append-only JSONL.
- It provides history, filtering, detail pages, and Markdown exports.
- It makes runtime failures visible as debugging evidence.

Lightwell is designed for local debugging and documentation, not for hiding the repair process.

## What Lightwell Is Not

- Not the adaptive-execution engine.
- Not the AI Execution Engine.
- Not a Docker runtime.
- Not a chatbot.
- Not a database-backed application.
- Not a replacement for production monitoring.
- Not a simulation of API results.

## Architecture

```text
User
  -> Lightwell /adaptive-run
  -> adaptive-execution API
  -> LLM proposes Python code
  -> AI Execution Engine runs code
  -> runtime stdout/stderr/exit_code
  -> adaptive-execution classifies and repairs
  -> Lightwell displays and persists the timeline
```

Lightwell does not decide correctness from model text. It records the result returned by adaptive execution, including code, stdout, stderr, exit code, error type, and repair strategy.

The important rule is:

```text
The LLM proposes code; the runtime determines truth.
```

## Adaptive Run Flow

The `/adaptive-run` page collects:

- `endpoint_url`
- `method`
- optional `json_body`

Lightwell builds the objective:

```text
Call this API endpoint using Python requests.
URL: {endpoint_url}
Method: {method}
Body: {json_body}
Print the full response and handle errors.
```

It sends this to:

```text
POST {ADAPTIVE_EXECUTION_API_URL}
```

with:

```json
{
  "objective": "...",
  "max_attempts": 3
}
```

Each attempt is shown in the Attempts Timeline with:

- attempt number
- status label
- error_type badge
- strategy badge
- exit_code
- generated code
- stdout
- stderr

HTTP failures are shown as API response failures instead of generic Python crashes when adaptive-execution returns HTTP-specific error types.

## History / Detail / Markdown Export

Adaptive runs are saved locally in:

```text
data/adaptive_runs.jsonl
```

Each entry stores:

- timestamp
- run_id
- endpoint_url
- method
- json_body
- generated objective
- success
- attempts
- final_error_type
- final_strategy

The history page is:

```text
/adaptive-runs/history
```

It supports server-side filters:

- `q`
- `method`
- `status`
- `error_type`

Each row links to a detail page:

```text
/adaptive-runs/history/<run_id>
```

The detail page shows a compact summary, run metadata, and the full attempts timeline.

Markdown export is available at:

```text
/adaptive-runs/history/<run_id>/markdown
```

Markdown exports are evidence artifacts for debugging and documentation. They are copy-friendly reports containing the generated objective, summary fields, and every attempt with code/stdout/stderr.

## Local Run Commands

Start the AI Execution Engine on port `8000`:

```powershell
cd C:\Users\slyse\Documents\ExecutionEngine\AIExecutionEngine
python -m uvicorn api:app --host 127.0.0.1 --port 8000
```

Start adaptive-execution on its configured port:

```powershell
cd C:\Users\slyse\Documents\adaptive-execution
python -m uvicorn api:app --host 127.0.0.1 --port 8880
```

Start Lightwell on port `8080`:

```powershell
cd C:\Users\slyse\Documents\agentcoding
python -m uvicorn app:app --host 127.0.0.1 --port 8080
```

Open:

```text
http://127.0.0.1:8080/adaptive-run
```

If adaptive-execution runs elsewhere, set:

```powershell
$env:ADAPTIVE_EXECUTION_API_URL="http://127.0.0.1:8880/adaptive-execution/run"
```

before starting Lightwell.

The older general run UI still exists:

```text
/
/history
/experiments
```

## Example Debugging Scenario

Input:

```text
endpoint_url = https://httpbin.org/status/404
method = GET
json_body =
```

Expected adaptive behavior:

```text
Attempt 1
  Runtime output shows HTTP 404.
  error_type = HTTPNotFound
  strategy = validate_endpoint

Attempt 2
  Repaired handling prints status_code and response.text safely.
  The run is labeled as repaired handling.
```

The point is not that a 404 becomes a successful API response. The point is that the failure no longer looks like an unknown crash. Lightwell shows the runtime evidence, the detected API failure, and the repair strategy.

## Current Limitations

- Local JSONL storage only.
- No database.
- No authentication.
- No background jobs.
- No hosted multi-user mode.
- No automatic cleanup or retention policy for `data/adaptive_runs.jsonl`.
- Markdown export is read-only and generated from stored JSONL.
- Full execution depends on adaptive-execution, the AI Execution Engine, and any required runtime services being available.
- API authentication is not managed by Lightwell; auth requirements must be represented in the endpoint or generated code context.
