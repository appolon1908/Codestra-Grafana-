"""Read-only client for the Middleware monitoring authority."""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


class MiddlewareUnavailable(RuntimeError):
    """Raised when the configured Middleware authority cannot be reached."""


class MiddlewareClient:
    """Call only read endpoints owned by Middleware; never stores platform state."""

    def __init__(self, base_url: str | None = None, token: str | None = None, timeout: float = 5.0):
        self.base_url = (base_url or os.getenv("CODESTRA_MIDDLEWARE_URL", "")).rstrip("/")
        self.token = token or self._load_token()
        self.timeout = timeout

    @staticmethod
    def _load_token() -> str:
        token_file = os.getenv("CODESTRA_MIDDLEWARE_TOKEN_FILE", "")
        if token_file:
            return Path(token_file).read_text(encoding="utf-8").strip()
        return os.getenv("CODESTRA_MIDDLEWARE_TOKEN", "").strip()

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.token)

    def _get(self, path: str, query: dict[str, str] | None = None) -> dict:
        if not self.configured:
            raise MiddlewareUnavailable("Middleware URL and bearer token are required")
        url = self.base_url + path
        if query:
            url += "?" + urlencode(query)
        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "Authorization": "Bearer " + self.token,
            },
            method="GET",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read())
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise MiddlewareUnavailable("Middleware monitoring authority unavailable") from exc
        if not isinstance(payload, dict):
            raise MiddlewareUnavailable("Middleware returned an invalid response")
        return payload

    def repositories(self) -> dict:
        return self._get("/platform/v1/repositories")

    def service_dependencies(self, service_id: str) -> dict:
        return self._get("/platform/v1/services/" + quote(service_id, safe="") + "/dependencies")

    def service_endpoints(self, service_id: str, environment: str) -> dict:
        return self._get(
            "/platform/v1/services/" + quote(service_id, safe="") + "/endpoints",
            {"environment": environment},
        )

    def service_coverage(self, service_id: str, environment: str) -> dict:
        return self._get(
            "/platform/v1/services/" + quote(service_id, safe="") + "/coverage",
            {"environment": environment},
        )

    def overview(self) -> dict:
        return self._get("/v1/observability/overview")

    def service_health(self, service_id: str, environment: str) -> dict:
        return self._get(
            "/v1/observability/services/" + quote(service_id, safe="") + "/health",
            {"environment": environment},
        )

    def topology(self) -> dict:
        return self._get("/v1/observability/topology")

    def integrations(self, integration_id: str | None = None) -> dict:
        path = "/v1/observability/integrations"
        if integration_id:
            path += "/" + quote(integration_id, safe="")
        return self._get(path)