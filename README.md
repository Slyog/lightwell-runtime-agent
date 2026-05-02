# Lightwell Runtime Agent

A local agent workspace that sends coding objectives to an external AI Execution Engine, observes real runtime results, classifies failures, and stores execution traces.

Lightwell Runtime Agent is the control, UI, and observation layer. The AI Execution Engine is the runtime backend that generates code, executes it in Docker, repairs failures, and returns `trace_ids`, `stdout`, and `stderr`.

## What This Proves

- The playground treats the execution engine as an external black-box API.
- It validates agent behavior through real runtime responses.
- It separates agent/code failures from infrastructure failures.
- It records JSONL traces for later inspection.
- It supports predefined experiments for repeatable behavior testing.
- It can summarize logs into readable outcomes.

## Current Limitation

- Docker must be available for full sandbox execution.
- If Docker is unavailable, the playground still correctly classifies it as infrastructure failure.

## Start The Engine

The local engine was found at:

```powershell
C:\Users\slyse\Documents\ExecutionEngine\AIExecutionEngine
```

Install its dependencies if needed:

```powershell
cd C:\Users\slyse\Documents\ExecutionEngine\AIExecutionEngine
python -m pip install -r requirements.txt
```

Start it with the documented command:

```powershell
python -m uvicorn api:app --host 127.0.0.1 --port 8000
```

The live `/agent-runs` schema expects:

```json
{
  "objective": "task text",
  "max_attempts": 1
}
```

The response includes `status`, `attempts`, `final_stdout`, `final_stderr`, and `trace_ids`.

## Run The Playground

From this directory:

```powershell
cd C:\Users\slyse\Documents\agentcoding
python runner.py "Write Python code that prints hello from the real engine" --mode single --base-url http://127.0.0.1:8000 --log real-validation-single.jsonl
python runner.py "Write Python code that imports pandas and prints a fallback message if pandas is unavailable" --mode retry_heavy --base-url http://127.0.0.1:8000 --log real-validation-retry-heavy.jsonl
python runner.py --experiment missing_dependency --mode retry_heavy --base-url http://127.0.0.1:8000 --log experiment-missing-dependency.jsonl
```

Available modes:

- `single`: one planned playground step, with one engine attempt per request.
- `retry_heavy`: up to five playground attempts; each request asks the engine for up to five internal attempts.
- `variation`: runs three slight phrasings and compares outcomes.

## Local UI

Start the Lightwell UI with:

```powershell
python -m uvicorn app:app --host 127.0.0.1 --port 8080
```

Open:

```text
http://127.0.0.1:8080
```

The UI submits objectives or predefined experiments through the existing playground runner and writes JSONL logs in this directory. `/history` lists JSONL logs and opens summaries using `summarize.py`.

## Experiments

Predefined experiments live in `experiments/registry.py`. They are just named objectives:

- `missing_dependency`
- `file_assumption`
- `timeout`
- `standard_library_fallback`
- `syntax_simple`

Run one with:

```powershell
python runner.py --experiment missing_dependency --mode retry_heavy --base-url http://127.0.0.1:8000
```

Direct task input still works:

```powershell
python runner.py "Write Python code that prints hello" --mode single --base-url http://127.0.0.1:8000
```

## Summarize Logs

Summarize one JSONL log with:

```powershell
python summarize.py real-validation-classified.jsonl
```

The summary reports total events, experiment name when present, final success, failure category, whether the failure is agent/code or infrastructure, trace count, and short stdout/stderr previews. Older logs with missing classification fields are classified from recorded stderr when possible.

## Validation Result

Validation against the real engine reached `POST /agent-runs` successfully.

- `single` produced trace id `12ad7196-2a49-412e-aba8-d0becb26eb22`.
- `retry_heavy` produced 25 engine trace ids across five playground attempts.
- JSONL logs recorded execute and observe events, including status, stdout, stderr, timeout state, and trace ids.

The engine did not complete Python execution because Docker Desktop's Linux engine was not reachable:

```text
failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine
```

That is an environment constraint, not a playground schema failure. The playground correctly reported `success: false`, preserved stdout/stderr, and surfaced trace ids from the real engine.

## Known Infrastructure Failure: Docker Engine Unavailable

When the engine returns stderr like:

```text
failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine
```

the playground classifies the run as:

```json
{
  "failure_category": "docker_unavailable",
  "is_agent_failure": false,
  "is_infrastructure_failure": true
}
```

This means the request reached `/agent-runs`, but the engine could not start its Docker-backed sandbox. It is not counted as an agent/code failure.
