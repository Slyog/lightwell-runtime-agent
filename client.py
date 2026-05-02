import json
import os
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional
from urllib import error, request


class AgentRunError(RuntimeError):
    pass


@dataclass
class AgentRunResult:
    trace_id: str
    trace_ids: list[str]
    stdout: str
    stderr: str
    exit_code: Optional[int]
    timeout: bool
    status: str
    raw: Dict[str, Any]

    @property
    def succeeded(self) -> bool:
        if self.status:
            return self.status == "completed"
        return self.exit_code == 0 and not self.timeout


class AgentRunClient:
    def __init__(self, base_url: Optional[str] = None, timeout_seconds: int = 60):
        self.base_url = (base_url or os.environ.get("AI_EXECUTION_ENGINE_URL") or "http://127.0.0.1:8000").rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._reachability_checked = False

    def check_reachable(self) -> None:
        if self._reachability_checked:
            return

        last_error = ""
        for path in ("/", "/docs"):
            req = request.Request(f"{self.base_url}{path}", method="GET")
            try:
                with request.urlopen(req, timeout=min(self.timeout_seconds, 10)):
                    self._reachability_checked = True
                    return
            except error.HTTPError:
                self._reachability_checked = True
                return
            except error.URLError as exc:
                last_error = str(exc.reason)
            except TimeoutError:
                last_error = "request timed out"

        raise AgentRunError(f"engine_unreachable: {last_error or self.base_url}")

    def run(self, task: str, max_attempts: int = 1) -> AgentRunResult:
        self.check_reachable()
        trace_id = str(uuid.uuid4())
        payload = json.dumps({"objective": task, "max_attempts": max_attempts}).encode("utf-8")
        req = request.Request(
            f"{self.base_url}/agent-runs",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with request.urlopen(req, timeout=self.timeout_seconds) as response:
                body = response.read().decode("utf-8")
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise AgentRunError(f"HTTP {exc.code}: {detail}") from exc
        except error.URLError as exc:
            raise AgentRunError(f"connection failed: {exc.reason}") from exc
        except TimeoutError as exc:
            raise AgentRunError("request timed out") from exc

        try:
            data = json.loads(body)
        except json.JSONDecodeError as exc:
            raise AgentRunError(f"invalid JSON response: {body[:200]}") from exc

        trace_ids = data.get("trace_ids")
        if isinstance(trace_ids, list):
            trace_ids = [str(item) for item in trace_ids]
        else:
            trace_ids = []
        returned_trace_id = str(data.get("trace_id") or data.get("id") or (trace_ids[0] if trace_ids else trace_id))
        timeout = bool(data.get("timeout") or data.get("timed_out"))
        exit_code = data.get("exit_code")
        if exit_code is not None:
            try:
                exit_code = int(exit_code)
            except (TypeError, ValueError):
                exit_code = None

        return AgentRunResult(
            trace_id=returned_trace_id,
            trace_ids=trace_ids or [returned_trace_id],
            stdout=str(data.get("stdout") or data.get("final_stdout") or ""),
            stderr=str(data.get("stderr") or data.get("final_stderr") or data.get("last_error") or ""),
            exit_code=exit_code,
            timeout=timeout,
            status=str(data.get("status") or ""),
            raw=data,
        )
