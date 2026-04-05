"""HTTP client for the shared planner/helper service."""
from __future__ import annotations

import json
from typing import Any, Iterator
from urllib import error, request


class PlannerClientError(RuntimeError):
    """Raised when the shared planner/helper service returns an error."""


class PlannerClient:
    """Small stdlib-only HTTP client for the main assistant backend."""

    def __init__(self, base_url: str = "http://127.0.0.1:8000", timeout: float = 60.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = float(timeout)

    def _url(self, path: str) -> str:
        normalized = path if path.startswith("/") else f"/{path}"
        return f"{self.base_url}{normalized}"

    def _open(
        self,
        path: str,
        method: str = "GET",
        payload: Any | None = None,
        *,
        accept: str = "application/json",
        timeout: float | None = None,
    ):
        data = None
        headers = {"Accept": accept}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = request.Request(self._url(path), data=data, headers=headers, method=method)
        try:
            return request.urlopen(req, timeout=timeout or self.timeout)
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise PlannerClientError(f"{method} {path} failed with {exc.code}: {detail}") from exc
        except Exception as exc:
            raise PlannerClientError(f"{method} {path} failed: {exc}") from exc

    def _request_json(
        self,
        path: str,
        method: str = "GET",
        payload: Any | None = None,
        *,
        timeout: float | None = None,
    ) -> Any:
        with self._open(path, method=method, payload=payload, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
        if not body.strip():
            return {}
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return {"raw_text": body}

    def get_policy(self) -> Any:
        return self._request_json("/planner/policy", method="GET")

    def get_health(self) -> Any:
        return self._request_json("/health", method="GET")

    def set_policy(self, payload: dict[str, Any]) -> Any:
        return self._request_json("/planner/policy", method="POST", payload=payload)

    def get_models(self) -> Any:
        return self._request_json("/planner/models", method="GET")

    def run_research(self, payload: dict[str, Any]) -> Any:
        return self._request_json("/planner/research/run", method="POST", payload=payload, timeout=max(self.timeout, 180.0))

    def helper_process_stream(self, payload: dict[str, Any], timeout: float | None = None) -> Iterator[dict[str, Any]]:
        request_timeout = timeout or max(self.timeout, 300.0)
        with self._open(
            "/helper/process",
            method="POST",
            payload=payload,
            accept="application/x-ndjson, application/json",
            timeout=request_timeout,
        ) as response:
            content_type = response.headers.get("Content-Type", "")
            if "application/x-ndjson" in content_type or "ndjson" in content_type:
                for raw_line in response:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        yield {"event": "text", "data": line}
                return

            body = response.read().decode("utf-8", errors="replace")
            if not body.strip():
                return

            try:
                parsed = json.loads(body)
            except json.JSONDecodeError:
                for line in body.splitlines():
                    text = line.strip()
                    if text:
                        yield {"event": "text", "data": text}
                return

            if isinstance(parsed, list):
                for item in parsed:
                    if isinstance(item, dict):
                        yield item
                    else:
                        yield {"event": "result", "data": item}
            elif isinstance(parsed, dict):
                yield parsed
            else:
                yield {"event": "result", "data": parsed}
