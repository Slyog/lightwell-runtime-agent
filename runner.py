import argparse
import json
from typing import List, Optional

from client import AgentRunClient, AgentRunError, AgentRunResult
from experiments import get_experiment, list_experiments
from logger import JsonlLogger


MAX_ATTEMPTS = 5
INFRASTRUCTURE_FAILURES = {"engine_unreachable", "docker_unavailable", "sandbox_start_failed"}
AGENT_FAILURES = {"timeout", "missing_dependency", "syntax_error", "runtime_error"}


def plan_steps(task: str) -> List[str]:
    lowered = task.lower()
    if (" file" in lowered or "files" in lowered) and (" output" in lowered or "process" in lowered):
        return [
            f"Check inputs and constraints for this task, handling missing files gracefully: {task}",
            f"Implement and execute the smallest working solution for: {task}",
        ]
    return [task]


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
    if "connection failed" in text:
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


def classify_error(result: Optional[AgentRunResult], exception: Optional[Exception] = None) -> Optional[str]:
    if exception is not None:
        text = str(exception).lower()
        if "engine_unreachable" in text:
            return "engine_unreachable"
        if "timed out" in text:
            return "timeout"
        if "connection failed" in text:
            return "sandbox_start_failed"
        if "http " in text:
            return "sandbox_start_failed"
        return "unknown_error"
    if result is None or result.succeeded:
        return None
    return classify_failure(result.stderr, result.timeout, result.status)


def adapt_task(task: str, result: Optional[AgentRunResult], error_type: Optional[str], attempt: int) -> str:
    if error_type == "missing_dependency":
        return f"{task}\nPrevious attempt failed due to a missing dependency. Retry using only the Python standard library."
    if error_type == "missing_file":
        return f"{task}\nPrevious attempt failed because a file was missing. Retry by checking existence first and simulating or handling the missing file gracefully."
    if error_type == "timeout":
        return f"{task}\nPrevious attempt timed out. Retry with a simpler, faster approach and smaller data."
    if error_type in INFRASTRUCTURE_FAILURES:
        return f"{task}\nPrevious attempt failed because infrastructure was unavailable ({error_type}). Do not treat this as a code-generation failure."
    if result is not None:
        return (
            f"{task}\nPrevious attempt {attempt} failed with exit_code={result.exit_code}, "
            f"timeout={result.timeout}, stderr={result.stderr[:500]!r}. Fix that specific failure."
        )
    return f"{task}\nPrevious attempt failed before execution reached the sandbox. Try a simpler direct solution."


def variation_tasks(task: str) -> List[str]:
    return [
        task,
        f"Solve this with the simplest possible Python standard-library code: {task}",
        f"Write and run a compact Python solution. Handle missing files, missing libraries, and timeouts gracefully: {task}",
    ]


def log_event(logger: JsonlLogger, event: str, payload: dict, experiment: Optional[str]) -> None:
    if experiment:
        payload = {**payload, "experiment": experiment}
    logger.log(event, payload)


def run_task(
    task: str,
    mode: str,
    client: AgentRunClient,
    logger: JsonlLogger,
    experiment: Optional[str] = None,
    max_attempts_override: Optional[int] = None,
    allow_network: bool = False,
) -> dict:
    steps = variation_tasks(task) if mode == "variation" else plan_steps(task)
    max_attempts = MAX_ATTEMPTS if mode == "retry_heavy" else len(steps)
    attempts = 0
    trace_ids: List[str] = []
    error_types: List[str] = []
    observations: List[str] = [f"Planned {len(steps)} step(s) for mode={mode}."]
    if experiment:
        observations.append(f"Using experiment: {experiment}.")
    final_stdout = ""
    final_stderr = ""
    final_api_signals = None

    for step_index, original_step in enumerate(steps, start=1):
        current_task = original_step
        step_attempts = max_attempts if mode == "retry_heavy" else 1

        for local_attempt in range(1, step_attempts + 1):
            attempts += 1
            log_event(logger, "execute", {"attempt": attempts, "step": step_index, "task": current_task}, experiment)

            try:
                engine_max_attempts = max_attempts_override if max_attempts_override is not None else (5 if mode == "retry_heavy" else 1)
                result = client.run(current_task, max_attempts=engine_max_attempts, allow_network=allow_network)
            except AgentRunError as exc:
                error_type = classify_error(None, exc)
                error_types.append(error_type or "unknown_error")
                observations.append(f"Attempt {attempts} failed before sandbox result: {exc}.")
                log_event(logger, "failure", {"attempt": attempts, "error_type": error_type, "error": str(exc)}, experiment)
                if error_type in INFRASTRUCTURE_FAILURES or mode != "retry_heavy" or local_attempt == step_attempts:
                    return finalize(task, steps, attempts, False, trace_ids, final_stdout, str(exc), error_types, observations, experiment, allow_network)
                current_task = adapt_task(current_task, None, error_type, attempts)
                observations.append(f"Changed strategy after {error_type}.")
                continue

            trace_ids.extend(item for item in result.trace_ids if item not in trace_ids)
            final_stdout = result.stdout
            final_stderr = result.stderr
            final_api_signals = result.raw.get("api_signals") if isinstance(result.raw, dict) else None
            failure_category = None if result.succeeded else classify_error(result)
            log_event(
                logger,
                "observe",
                {
                    "attempt": attempts,
                    "trace_id": result.trace_id,
                    "trace_ids": result.trace_ids,
                    "status": result.status,
                    "exit_code": result.exit_code,
                    "timeout": result.timeout,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "failure_category": failure_category,
                    "is_agent_failure": failure_category in AGENT_FAILURES,
                    "is_infrastructure_failure": failure_category in INFRASTRUCTURE_FAILURES,
                },
                experiment,
            )

            if result.succeeded:
                observations.append(f"Attempt {attempts} succeeded with exit_code=0.")
                if mode == "variation":
                    continue
                if step_index == len(steps):
                    return finalize(task, steps, attempts, True, trace_ids, final_stdout, final_stderr, error_types, observations, experiment, allow_network, final_api_signals)
                break

            error_type = classify_error(result)
            error_types.append(error_type or "unknown_error")
            observations.append(
                f"Attempt {attempts} failed with {error_type}; exit_code={result.exit_code}, timeout={result.timeout}."
            )
            if error_type in INFRASTRUCTURE_FAILURES:
                return finalize(task, steps, attempts, False, trace_ids, final_stdout, final_stderr, error_types, observations, experiment, allow_network, final_api_signals)
            if mode == "variation":
                continue
            if mode != "retry_heavy" or local_attempt == step_attempts:
                return finalize(task, steps, attempts, False, trace_ids, final_stdout, final_stderr, error_types, observations, experiment, allow_network, final_api_signals)
            current_task = adapt_task(current_task, result, error_type, attempts)
            observations.append(f"Changed strategy after {error_type}.")

    success = bool(trace_ids) and not final_stderr and (not error_types or mode == "variation")
    if mode == "variation":
        success = bool(trace_ids) and any("succeeded" in item for item in observations)
    return finalize(task, steps, attempts, success, trace_ids, final_stdout, final_stderr, error_types, observations, experiment, allow_network, final_api_signals)


def finalize(
    task: str,
    steps: List[str],
    attempts: int,
    success: bool,
    trace_ids: List[str],
    final_stdout: str,
    final_stderr: str,
    error_types: List[str],
    observations: List[str],
    experiment: Optional[str] = None,
    allow_network: bool = False,
    api_signals: Optional[dict] = None,
) -> dict:
    failure_category = "none" if success else classify_failure(final_stderr)
    is_infrastructure_failure = failure_category in INFRASTRUCTURE_FAILURES
    is_agent_failure = failure_category in AGENT_FAILURES
    output = {
        "task": task,
        "steps": steps,
        "attempts": attempts,
        "success": success,
        "failure_category": failure_category,
        "is_agent_failure": is_agent_failure,
        "is_infrastructure_failure": is_infrastructure_failure,
        "trace_ids": trace_ids,
        "final_stdout": final_stdout,
        "final_stderr": final_stderr,
        "error_types": error_types,
        "observations": observations,
        "allow_network": allow_network,
    }
    if isinstance(api_signals, dict):
        output["api_signals"] = api_signals
    if experiment:
        output["experiment"] = experiment
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="Minimal execution-grounded co-worker runner.")
    parser.add_argument("task", nargs="?")
    parser.add_argument("--experiment", choices=list_experiments())
    parser.add_argument("--mode", choices=["single", "retry_heavy", "variation"], default="single")
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--log", default="agent_runs.jsonl")
    args = parser.parse_args()

    if args.experiment:
        task = get_experiment(args.experiment)
    elif args.task:
        task = args.task
    else:
        parser.error("provide a task or --experiment")

    client = AgentRunClient(args.base_url)
    logger = JsonlLogger(args.log)
    result = run_task(task, args.mode, client, logger, args.experiment)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
