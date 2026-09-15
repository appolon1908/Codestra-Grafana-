#!/usr/bin/env python3
"""Small, dependency-free control-plane API for the Codestra observability registry."""

from __future__ import annotations

import argparse
import json
import os
import threading
from dataclasses import asdict, dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .middleware_client import MiddlewareClient, MiddlewareUnavailable


ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = ROOT / "codestra" / "business-registry.json"
ONBOARDING_PATH = ROOT / "codestra" / "onboarding" / "grafana-bindings.v1.json"
API_PREFIX = "/platform/v1"
REQUIRED_SERVICE_FIELDS = {"id", "name", "type", "owner"}
ALLOWED_SERVICE_TYPES = {"application", "platform"}
DEFAULT_OBSERVABILITY = {
    "prometheus": True,
    "alloy": True,
    "loki": True,
    "tempo": True,
    "grafana": True,
    "alertmanager": True,
}


class ValidationError(ValueError):
    """Raised when an API resource does not satisfy its contract."""


@dataclass
class Service:
    id: str
    name: str
    type: str
    owner: str
    environments: list[str] = field(default_factory=lambda: ["staging", "production"])
    dependencies: list[str] = field(default_factory=list)
    observability: dict[str, bool] = field(
        default_factory=lambda: DEFAULT_OBSERVABILITY.copy()
    )
    status: str = "DISCOVERED_NOT_YET_ONBOARDED"


def _service_from_payload(payload: dict[str, Any]) -> Service:
    missing = REQUIRED_SERVICE_FIELDS - payload.keys()
    if missing:
        raise ValidationError(f"missing required fields: {', '.join(sorted(missing))}")
    if not isinstance(payload["id"], str) or not payload["id"].strip():
        raise ValidationError("id must be a non-empty string")
    if payload["type"] not in ALLOWED_SERVICE_TYPES:
        raise ValidationError("type must be application or platform")
    environments = payload.get("environments", ["staging", "production"])
    dependencies = payload.get("dependencies", [])
    if not isinstance(environments, list) or not all(isinstance(item, str) for item in environments):
        raise ValidationError("environments must be a list of strings")
    if not isinstance(dependencies, list) or not all(isinstance(item, str) for item in dependencies):
        raise ValidationError("dependencies must be a list of strings")
    observability = payload.get("observability")
    if observability is None:
        observability = DEFAULT_OBSERVABILITY.copy()
    if not isinstance(observability, dict) or not all(
        isinstance(value, bool) for value in observability.values()
    ):
        raise ValidationError("observability must be an object of boolean values")
    return Service(
        id=payload["id"].strip(),
        name=str(payload["name"]).strip(),
        type=payload["type"],
        owner=str(payload["owner"]).strip(),
        environments=environments,
        dependencies=dependencies,
        observability=observability,
        status=payload.get("status", "DISCOVERED_NOT_YET_ONBOARDED"),
    )


class Catalog:
    """Read-only registry facade used by the Grafana repository for validation and discovery."""

    def __init__(
        self,
        registry_path: Path = REGISTRY_PATH,
        onboarding_path: Path = ONBOARDING_PATH,
        middleware: MiddlewareClient | None = None,
    ) -> None:
        self.registry_path = registry_path
        self.onboarding_path = onboarding_path
        self.middleware = middleware or MiddlewareClient()
        self._lock = threading.RLock()
        self._services = self._load_registry()
        self._bindings = self._load_onboarding_bindings()

    def _load_registry(self) -> dict[str, Service]:
        document = json.loads(self.registry_path.read_text(encoding="utf-8"))
        services: dict[str, Service] = {}
        for business in document.get("businesses", []):
            for repository in business.get("repositories", []):
                service = Service(
                    id=repository["service"],
                    name=repository["service"],
                    type="application",
                    owner=business["team"],
                )
                services[service.id] = service
        for platform in document.get("platform_services", []):
            service = Service(
                id=platform["service"],
                name=platform["service"],
                type="platform",
                owner="platform",
            )
            services[service.id] = service
        return services

    def _load_onboarding_bindings(self) -> dict[str, dict[str, Any]]:
        if not self.onboarding_path.exists():
            return {}
        document = json.loads(self.onboarding_path.read_text(encoding="utf-8"))
        bindings: dict[str, dict[str, Any]] = {}
        for binding in document.get("bindings", []):
            service_id = binding.get("service_id")
            if isinstance(service_id, str):
                bindings[service_id] = binding
        return bindings

    def list_services(self) -> list[dict[str, Any]]:
        if self.middleware.configured:
            return self.middleware.repositories().get("data", [])
        with self._lock:
            return [asdict(service) for service in sorted(self._services.values(), key=lambda item: item.id)]

    def get_service(self, service_id: str) -> dict[str, Any]:
        with self._lock:
            service = self._services.get(service_id)
            if not service:
                raise KeyError(service_id)
            return asdict(service)

    def dependencies(self, service_id: str) -> list[str]:
        if self.middleware.configured:
            payload = self.middleware.service_dependencies(service_id)
            data = payload.get("data", {})
            return data.get("declared", []) if isinstance(data, dict) else []
        return self.get_service(service_id)["dependencies"]

    def validate(self, service_id: str) -> dict[str, Any]:
        service = self.get_service(service_id)
        errors: list[str] = []
        unknown = [dependency for dependency in service["dependencies"] if dependency not in self._services]
        for dependency in unknown:
            errors.append(f"unknown dependency: {dependency}")

        binding = self._bindings.get(service_id)
        if binding is None:
            errors.append("missing onboarding binding")
        else:
            if not binding.get("dashboard"):
                errors.append(f"missing dashboard binding for {service_id}")
            if set(binding.get("signals", [])) != {"metrics", "logs", "alerts"}:
                errors.append(f"incomplete signal contract for {service_id}")
            if not binding.get("dependencies"):
                errors.append(f"missing dependency list in onboarding binding for {service_id}")

        required_observability = {"prometheus", "loki", "tempo", "alloy", "grafana", "alertmanager"}
        missing_observability = [
            key for key in sorted(required_observability) if not service["observability"].get(key, False)
        ]
        if missing_observability:
            errors.append(f"disabled observability for {service_id}: {', '.join(missing_observability)}")

        if not service["dependencies"]:
            errors.append(f"missing dependency declaration for {service_id}")

        return {
            "service_id": service_id,
            "valid": not errors,
            "errors": errors,
            "production_activation": False,
        }

    def status(self, service_id: str, environment: str = "production") -> dict[str, Any]:
        if self.middleware.configured:
            return self.middleware.service_coverage(service_id, environment)
        return {"service_id": service_id, "status": self.get_service(service_id)["status"], "production_activation": False}

    def platform_health(self) -> dict[str, Any]:
        if self.middleware.configured:
            return self.middleware.overview()
        return {"status": "healthy", "production_activation": False}

    def secrets_health(self) -> dict[str, Any]:
        # OpenBao is opaque to Grafana; there is no local fallback because
        # Grafana never queries OpenBao directly (Middleware owns secrets).
        if not self.middleware.configured:
            raise MiddlewareUnavailable("Middleware URL and bearer token are required for secrets health")
        return self.middleware.secrets_health()

    def onboard(self, service_id: str) -> dict[str, Any]:
        validation = self.validate(service_id)
        if not validation["valid"]:
            return {**validation, "prepared": False}
        return {
            **validation,
            "prepared": True,
            "actions": ["read-only service manifest", "metrics target", "log mapping", "trace mapping", "dashboard binding"],
        }

    def integrations_list(self) -> dict[str, Any]:
        # Middleware's /v1/observability/integrations already returns every
        # integration registered/observed for the caller's tenant in one call;
        # it is keyed by integration_id, not by service_id, so it must never
        # be driven by iterating over list_services().
        if self.middleware.configured:
            return self.middleware.integrations()
        return {
            "integrations": [
                self.integration(service_id)
                for service_id in sorted(self._services)
            ]
        }

    def integration(self, integration_id: str) -> dict[str, Any]:
        # integration_id identifies a configured backend binding (e.g.
        # "backstage", "sentry", "wazuh") and is distinct from service_id.
        if self.middleware.configured:
            return self.middleware.integrations(integration_id)
        service = self.get_service(integration_id)
        return {
            "service_id": integration_id,
            "environment": "staging",
            "metrics": "configured" if service["observability"]["prometheus"] else "disabled",
            "logs": "configured" if service["observability"]["loki"] else "disabled",
            "traces": "configured" if service["observability"]["tempo"] else "disabled",
            "health_probe": "configured",
            **service["observability"],
        }


class Handler(BaseHTTPRequestHandler):
    catalog: Catalog

    def _send(self, status: int, payload: Any) -> None:
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError as exc:
            raise ValidationError("request body must be valid JSON") from exc
        if not isinstance(payload, dict):
            raise ValidationError("request body must be a JSON object")
        return payload

    def _route(self) -> list[str]:
        path = urlparse(self.path).path
        if not path.startswith(API_PREFIX):
            raise KeyError(path)
        return [part for part in path[len(API_PREFIX):].split("/") if part]

    def do_GET(self) -> None:  # noqa: N802
        try:
            route = self._route()
            if route == ["services"]:
                return self._send(200, {"services": self.catalog.list_services()})
            if len(route) == 2 and route[0] == "services":
                return self._send(200, self.catalog.get_service(route[1]))
            if len(route) == 3 and route[0] == "services" and route[2] == "dependencies":
                return self._send(200, {"service_id": route[1], "dependencies": self.catalog.dependencies(route[1])})
            if len(route) == 3 and route[0] == "services" and route[2] == "status":
                return self._send(200, self.catalog.status(route[1]))
            if route == ["observability", "health"]:
                return self._send(200, self.catalog.platform_health())
            if route == ["observability", "secrets", "health"]:
                return self._send(200, self.catalog.secrets_health())
            if route == ["observability", "readiness"]:
                return self._send(200, {"status": "ready", "production_activation": False})
            if route == ["observability", "targets"]:
                return self._send(200, {"targets": self.catalog.list_services()})
            if len(route) == 3 and route[:2] == ["observability", "targets"]:
                return self._send(200, self.catalog.get_service(route[2]))
            if route == ["observability", "integrations"]:
                return self._send(200, self.catalog.integrations_list())
            if len(route) == 3 and route[:2] == ["observability", "integrations"]:
                return self._send(200, self.catalog.integration(route[2]))
            raise KeyError(self.path)
        except KeyError:
            self._send(404, {"error": "resource not found"})
        except MiddlewareUnavailable as exc:
            self._send(503, {"error": str(exc)})
        except ValidationError as exc:
            self._send(400, {"error": str(exc)})

    def do_POST(self) -> None:  # noqa: N802
        try:
            route = self._route()
            if len(route) == 3 and route[0] == "services" and route[2] == "validate":
                return self._send(200, self.catalog.validate(route[1]))
            if len(route) == 3 and route[0] == "services" and route[2] == "onboard":
                return self._send(200, self.catalog.onboard(route[1]))
            if (
                len(route) == 4
                and route[0:2] == ["observability", "integrations"]
                and route[3] == "validate"
            ):
                validation = self.catalog.validate(route[2])
                return self._send(200, {
                    "service_id": route[2],
                    "valid": validation["valid"],
                    "errors": validation["errors"],
                    "production_activation": False,
                })
            return self._send(405, {"error": "write operations are disabled in Grafana; use Middleware for mutating registry state"})
        except KeyError:
            self._send(404, {"error": "resource not found"})
        except MiddlewareUnavailable as exc:
            self._send(503, {"error": str(exc)})
        except ValidationError as exc:
            self._send(400, {"error": str(exc)})

    def do_PATCH(self) -> None:  # noqa: N802
        self._send(405, {"error": "write operations are disabled in Grafana; use Middleware for mutating registry state"})

    def log_message(self, format: str, *args: Any) -> None:
        return


def create_server(host: str = "127.0.0.1", port: int = 8081) -> ThreadingHTTPServer:
    normalized_host = host.strip() or "127.0.0.1"
    if normalized_host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("non-loopback bind is forbidden until explicit auth and RBAC are in place")
    Handler.catalog = Catalog()
    return ThreadingHTTPServer((normalized_host, port), Handler)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.getenv("CODESTRA_CONTROL_PLANE_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("CODESTRA_CONTROL_PLANE_PORT", "8081")))
    args = parser.parse_args()
    try:
        server = create_server(args.host, args.port)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(f"Codestra control plane listening on http://{args.host}:{args.port}{API_PREFIX}")
    server.serve_forever()


if __name__ == "__main__":
    main()
