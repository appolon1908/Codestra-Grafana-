"""Staging-only end-to-end harness: real Middleware monitoring routes served over
a real loopback HTTP/uvicorn server, real Alembic migration 0059, a real local
JWKS endpoint, and real RS256 bearer tokens -- exercised through this repository's
own MiddlewareClient exactly as Grafana would call it in production.

This is NOT a mock. It imports Middleware's actual `app.monitoring.routes` router,
`app.monitoring.auth`, `app.monitoring.backends`, and `app.monitoring.store` from a
local checkout of the Middleware- repository, runs Alembic migration
`0059_integrated_monitoring` against a real (disposable) database, and serves the
app over a real TCP loopback socket so this repository's HTTP client code path is
exercised unmodified.

Requirements:
  - A local checkout of appolon1908-hue/Middleware- (path via MIDDLEWARE_REPO_PATH
    env var, default: sibling directory "Middleware-" next to this repo).
  - Middleware's own Python dependencies installed (fastapi, uvicorn, sqlalchemy,
    alembic, pyjwt, cryptography, httpx, aiosqlite are sufficient for this harness;
    asyncpg/postgres is used automatically if MONITORING_TEST_DATABASE_URL is set).

Usage:
    python tests\\e2e\\staging_harness.py
Exit code 0 = all staging checks passed. Non-zero = failure, with details on stderr.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import importlib.util
import json
import os
import socket
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MIDDLEWARE_PATH = REPO_ROOT.parent / "Middleware-"


def _fail(message: str) -> None:
    print(f"[staging-harness] FAIL: {message}", file=sys.stderr)
    sys.exit(1)


def _resolve_middleware_path() -> Path:
    configured = os.getenv("MIDDLEWARE_REPO_PATH", "")
    path = Path(configured) if configured else DEFAULT_MIDDLEWARE_PATH
    if not (path / "app" / "monitoring" / "routes.py").exists():
        _fail(
            "Middleware- checkout not found at "
            f"{path}. Set MIDDLEWARE_REPO_PATH to a local checkout of "
            "appolon1908-hue/Middleware- to run the staging harness."
        )
    return path


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _generate_rsa_keypair():
    from cryptography.hazmat.primitives.asymmetric import rsa

    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _jwk_from_public_key(public_key, kid: str) -> dict:
    numbers = public_key.public_numbers()

    def b64url(value: int) -> str:
        import base64

        raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    return {
        "kty": "RSA",
        "use": "sig",
        "alg": "RS256",
        "kid": kid,
        "n": b64url(numbers.n),
        "e": b64url(numbers.e),
    }


def _start_jwks_server(jwks_document: dict):
    """Real HTTP server serving a JWKS document on loopback -- used by the
    real PyJWKClient inside app.core.jwt_auth.KeycloakValidator, unmodified."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    body = json.dumps(jwks_document).encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    return server, thread, f"http://127.0.0.1:{port}/certs"


def _start_uvicorn(app, port: int):
    import uvicorn

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if getattr(server, "started", False):
            return server, thread
        time.sleep(0.05)
    _fail("Middleware staging server did not start within 15 seconds")


def run() -> None:
    middleware_path = _resolve_middleware_path()
    sys.path.insert(0, str(middleware_path))

    import jwt
    from fastapi import FastAPI
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.engine import make_url
    from sqlalchemy.pool import NullPool
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    import httpx

    from app.core.config import settings
    from app.monitoring.backends import Backends, load_config, load_github_secret
    from app.monitoring.routes import get_backends, get_session, router as monitoring_router

    tmp_root = Path(os.getenv("STAGING_HARNESS_TMP", "")) if os.getenv("STAGING_HARNESS_TMP") else None
    with contextlib.ExitStack() as stack:
        if tmp_root is None:
            import tempfile

            tmp_root = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="codestra-staging-")))
        tmp_root.mkdir(parents=True, exist_ok=True)

        # --- Real Keycloak-shaped auth: RSA keypair + real JWKS HTTP endpoint ---
        private_key = _generate_rsa_keypair()
        kid = "staging-harness-key-1"
        jwks_doc = {"keys": [_jwk_from_public_key(private_key.public_key(), kid)]}
        jwks_server, jwks_thread, jwks_url = _start_jwks_server(jwks_doc)
        stack.callback(jwks_server.shutdown)

        issuer = "https://identity.example.invalid/realms/staging"
        audience = "middleware-api"
        settings.keycloak_issuer = issuer
        settings.keycloak_audience = audience
        settings.keycloak_jwks_url = jwks_url
        settings.keycloak_authorized_parties = "operator,collector,browser-bff"

        def mint_token(role: str = "platform_admin", scopes: str = "") -> str:
            now = datetime.now(UTC).timestamp()
            claims = {
                "iss": issuer,
                "aud": audience,
                "azp": "operator",
                "sub": "staging-harness-subject",
                "iat": int(now),
                "exp": int(now) + 300,
                "tenant_id": "codestra-platform",
                "scope": scopes,
                "realm_access": {"roles": [role]},
                "campaigns": ["campaign-one"],
                "services": ["middleware", "odoo"],
            }
            return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": kid})

        all_scopes = " ".join(
            [
                "platform.services.read",
                "observability.health.read",
                "observability.integrations.read",
            ]
        )
        token = mint_token(scopes=all_scopes)

        # --- Real PostgreSQL migration 0059_integrated_monitoring, or disposable SQLite ---
        database_url = os.getenv("MONITORING_TEST_DATABASE_URL") or (
            "sqlite+aiosqlite:///" + str(tmp_root / "monitoring.db")
        )
        if database_url.startswith("postgresql"):
            database_name = make_url(database_url).database or ""
            if not database_name.startswith("monitoring_test"):
                _fail("MONITORING_TEST_DATABASE_URL must point at a disposable monitoring_test* database")
        engine = create_async_engine(database_url, poolclass=NullPool)

        def apply_migration(connection):
            with Operations.context(MigrationContext.configure(connection)):
                importlib.import_module("migrations.versions.0059_integrated_monitoring").upgrade()

        async def migrate():
            async with engine.begin() as connection:
                await connection.run_sync(apply_migration)

        asyncio.run(migrate())
        print("[staging-harness] Alembic migration 0059_integrated_monitoring applied to", database_url)

        from sqlalchemy.ext.asyncio import async_sessionmaker

        sessions = async_sessionmaker(engine, expire_on_commit=False)

        async def db_session():
            async with sessions() as session:
                yield session

        # --- Real mounted monitoring configuration ---
        secret_path = tmp_root / "github-secret"
        secret_path.write_text("staging-harness-github-secret-0000000000")
        fixture_path = REPO_ROOT / "codestra" / "staging" / "monitoring-config.fixture.json"
        config = json.loads(fixture_path.read_text(encoding="utf-8"))
        config["github"]["secret_file"] = str(secret_path)
        config_path = tmp_root / "monitoring-config.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        os.environ["MONITORING_CONFIG_FILE"] = str(config_path)

        app = FastAPI()
        app.include_router(monitoring_router)
        app.dependency_overrides[get_session] = db_session
        app.dependency_overrides[load_config] = lambda: config
        app.dependency_overrides[load_github_secret] = lambda: secret_path.read_bytes().strip()
        app.dependency_overrides[get_backends] = lambda: Backends(config, httpx.Client())

        port = _free_port()
        server, server_thread = _start_uvicorn(app, port)
        stack.callback(lambda: setattr(server, "should_exit", True))

        base_url = f"http://127.0.0.1:{port}"

        # --- Grafana's real client, wired at a real network path ---
        sys.path.insert(0, str(REPO_ROOT))
        from codestra.control_plane.middleware_client import MiddlewareClient, MiddlewareUnavailable

        client = MiddlewareClient(base_url=base_url, token=token, timeout=10.0)
        if not client.configured:
            _fail("MiddlewareClient did not report configured with a real base_url and token")

        checks: list[tuple[str, bool, str]] = []

        def check(name: str, fn) -> None:
            try:
                result = fn()
                checks.append((name, True, json.dumps(result)[:200]))
            except Exception as exc:  # noqa: BLE001
                checks.append((name, False, repr(exc)))

        check("repositories", client.repositories)
        check("service_dependencies(middleware)", lambda: client.service_dependencies("middleware"))
        check("overview", client.overview)
        check("topology", client.topology)
        check("service_health(odoo)", lambda: client.service_health("odoo", "staging"))

        # Negative case: unauthenticated / bad token must surface as a clean failure,
        # never a false success -- exercised through the real network path too.
        bad_client = MiddlewareClient(base_url=base_url, token="not-a-real-token", timeout=10.0)
        try:
            bad_client.repositories()
            checks.append(("rejects invalid bearer token", False, "expected MiddlewareUnavailable, got success"))
        except MiddlewareUnavailable:
            checks.append(("rejects invalid bearer token", True, "401/403 surfaced as MiddlewareUnavailable"))

        # Negative case: server unreachable must surface as MiddlewareUnavailable, not a crash.
        unreachable = MiddlewareClient(base_url="http://127.0.0.1:1", token=token, timeout=1.0)
        try:
            unreachable.repositories()
            checks.append(("surfaces unreachable Middleware", False, "expected MiddlewareUnavailable, got success"))
        except MiddlewareUnavailable:
            checks.append(("surfaces unreachable Middleware", True, "connection failure surfaced as MiddlewareUnavailable"))

        print()
        print("STAGING-ONLY END-TO-END HARNESS RESULTS")
        print("=" * 60)
        failed = False
        for name, ok, detail in checks:
            status = "PASS" if ok else "FAIL"
            if not ok:
                failed = True
            print(f"[{status}] {name}: {detail}")
        print("=" * 60)

        if failed:
            _fail("one or more staging end-to-end checks failed")
        print("[staging-harness] all checks passed")


if __name__ == "__main__":
    run()
