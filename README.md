# AI Execution System – Tracewell Runtime (UI Layer)

Tracewell Runtime is the UI and observability layer of a 3-part AI execution system.

This UI makes execution traces visible — so you can see how code fails, why it fails, and how it gets fixed.

It visualizes how code is executed, how failures are detected, and how repair decisions are applied across multiple attempts.

> The system does not guess correctness. It proves it through execution.

---

## System Overview

This repository is part of a 3-layer execution system:

| Layer | Role | Repo |
|---|---|---|
| **AI-Execution-Engine** | Executes Python code in an isolated Docker runtime. Produces `stdout`, `stderr`, `exit_code` as ground truth. | [Slyog/AI-Execution-Engine](https://github.com/Slyog/AI-Execution-Engine) |
| **adaptive-execution** | Interprets runtime failures. Maps failures to deterministic repair strategies. Retries until success. | [Slyog/adaptive-execution](https://github.com/Slyog/adaptive-execution) |
| **Tracewell Runtime** *(this repo)* | Visualizes execution traces, API signals, and decision flow. | [Slyog/execution-trace-ui](https://github.com/Slyog/execution-trace-ui) |

**Architecture:**

```text
User / Agent
→ Tracewell Runtime      (UI)
→ adaptive-execution     (decision layer)
→ AI-Execution-Engine    (runtime)
→ real API
```

---

## What This Layer Does

Tracewell Runtime provides:

- Execution timeline (attempts)
- API signals (auth, validation, network, success)
- Decision trace (why a retry happened)
- Full `stdout` / `stderr` visibility
- Persistent run history (JSONL)

It is not responsible for execution or decision-making — it only surfaces what actually happened.

---

## Core Behavior

```text
execute → observe → decision → execute → observe → decision → execute → observe
```

Each attempt in the system is:

1. Proposed by adaptive-execution
2. Executed in Docker by AI-Execution-Engine
3. Observed via real output
4. Interpreted into signals
5. Repaired deterministically

**Example:**

```text
Attempt 1 → failure
  POST /users returns 401
  auth_failure_observed = true

Attempt 2 → partial fix
  Authorization header added
  POST /users returns 400
  validation_failure_observed = true

Attempt 3 → success
  Payload fixed (age as integer)
  POST /users returns 200
  success_observed = true
```

---

## Adaptive Retry Demo

The adaptive retry demo starts from a broken API request and reaches a working request using runtime feedback.

**Sequence:**

1. Attempt 1 executes without `Authorization`
2. API returns `401`
3. System observes `auth_failure_observed = true`
4. Decision: add Authorization header
5. Attempt 2 executes with auth but invalid payload
6. API returns `400`
7. System observes `validation_failure_observed = true`
8. Decision: fix payload type
9. Attempt 3 executes with valid request
10. API returns `200`

**Final signals:**

```json
{
  "status_sequence": [401, 400, 200],
  "final_success": true,
  "failure_category": "none"
}
```

> There is no LLM decision-making in this path.  
> All retries are deterministic and based on observed runtime signals.

---

## Key Idea

> LLMs can propose actions.  
> Only execution determines if they are correct.

- Most tools infer correctness from code or model output
- This system executes real code against real APIs
- Failures are observed, not guessed
- Every repair step is traceable
- The final result is based on what actually ran

---

## Stack

- Python
- FastAPI
- HTML templates + CSS
- JSONL history
- `adaptive-execution` service
- AI-Execution-Engine (Docker runtime)

---

## How To Run (Local)

**1. Start AI-Execution-Engine:**

```powershell
cd C:\Users\slyse\Documents\AIEngine\AI-Execution-Engine
python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

**2. Start adaptive-execution:**

```powershell
cd C:\Users\slyse\Documents\adaptive-execution
python -m uvicorn api:app --host 127.0.0.1 --port 8880
```

**3. Start Tracewell Runtime:**

```powershell
cd C:\Users\slyse\Documents\agentcoding
python -m uvicorn app:app --host 127.0.0.1 --port 8080
```

**4. Open in browser:**

```
http://127.0.0.1:8080/adaptive-run
```

---

## Configuration

If `adaptive-execution` runs on a different host, set this before starting the UI:

```powershell
$env:ADAPTIVE_EXECUTION_API_URL="http://127.0.0.1:8880/adaptive-execution/run"
```

---

## Optional: Agent Integration

This system can be exposed as a tool (e.g. for OpenClaw):

```python
adaptive_execution_run(endpoint_url, method, objective, allow_network, max_attempts)
```

Agents can invoke execution — but correctness still comes from runtime.
