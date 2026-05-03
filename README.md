# AI that debugs broken code by executing it

Lightwell is a local UI for adaptive code execution.

It sends a debugging objective to `adaptive-execution`, shows each attempt, and records the result.

The key behavior is simple: code is generated, executed, observed, repaired, and retried.

Correctness comes from runtime output: `stdout`, `stderr`, `exit_code`, `error_type`, and the final attempt.

## Example

```text
Attempt 1 -> error
  POST /users returns 400
  error_type = HTTPBadRequest
  stderr = age must be an integer

Attempt 2 -> fix
  strategy = repair_request_body
  generated code sends age as an integer

Attempt 3 -> success
  success = true
  stdout = user created
```

## Adaptive Retry Demo

The adaptive retry demo starts from a broken API request and reaches a working request through deterministic runtime feedback.

Event flow:

```text
execute -> observe -> decision -> execute -> observe -> decision -> execute -> observe
```

1. Attempt 1 executes a request without an `Authorization` header.
2. The API returns `401`.
3. The system observes `auth_failure_observed=true`.
4. The decision step applies a deterministic rule: add the `Authorization` header.
5. Attempt 2 executes with authorization, but the payload still sends `age` as a string.
6. The API returns `400`.
7. The system observes `validation_failure_observed=true`.
8. The decision step applies a deterministic rule: fix the payload type.
9. Attempt 3 executes with authorization and a valid integer `age`.
10. The API returns `200`.

There is no LLM decision-making in this retry path. The retry rules are deterministic, and the Docker runtime executes real Python code for each attempt. Lightwell shows both the API Signals and the Adaptive Event Chain in the run detail page, so the demo visibly proves the sequence from failed auth to fixed validation to final success.

## Why This Matters

- Most tools guess from code or model output.
- This executes the code and observes real failures.
- Each repair attempt leaves evidence.
- The final result is based on what ran, not what the model claimed.

## Stack

- Python
- FastAPI
- HTML templates
- CSS
- Local JSONL history
- `adaptive-execution` service
- AI Execution Engine

## How To Run

Start the AI Execution Engine:

```powershell
cd C:\Users\slyse\Documents\ExecutionEngine\AIExecutionEngine
python -m uvicorn api:app --host 127.0.0.1 --port 8000
```

Start `adaptive-execution`:

```powershell
cd C:\Users\slyse\Documents\adaptive-execution
python -m uvicorn api:app --host 127.0.0.1 --port 8880
```

Start Lightwell:

```powershell
cd C:\Users\slyse\Documents\agentcoding
python -m uvicorn app:app --host 127.0.0.1 --port 8080
```

Open:

```text
http://127.0.0.1:8080/adaptive-run
```

If `adaptive-execution` runs somewhere else:

```powershell
$env:ADAPTIVE_EXECUTION_API_URL="http://127.0.0.1:8880/adaptive-execution/run"
```

Set that before starting Lightwell.
