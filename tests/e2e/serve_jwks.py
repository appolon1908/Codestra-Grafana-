"""Standalone real JWKS HTTP server for staging/manual use with docker-compose.staging.yml.

Generates a real RSA keypair, serves the public JWKS document over HTTP, and
prints a matching RS256 bearer token to stdout. Uses the exact same key
material and format as tests/e2e/staging_harness.py, so the same
KeycloakValidator code path in Middleware's app/core/jwt_auth.py validates it
unmodified.

This is a synthetic issuer for staging/dev use only. It is not Keycloak and
must never be pointed at from a production KEYCLOAK_ISSUER.
"""

from __future__ import annotations

import base64
import json
import sys
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

KID = "staging-harness-key-1"
ISSUER = "https://identity.example.invalid/realms/staging"
AUDIENCE = "middleware-api"


def _b64url(value: int) -> str:
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def main() -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    numbers = private_key.public_key().public_numbers()
    jwks_doc = {
        "keys": [
            {
                "kty": "RSA",
                "use": "sig",
                "alg": "RS256",
                "kid": KID,
                "n": _b64url(numbers.n),
                "e": _b64url(numbers.e),
            }
        ]
    }
    body = json.dumps(jwks_doc).encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8096
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)  # nosec B104 - staging-only, loopback/compose network

    now = datetime.now(UTC).timestamp()
    claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "azp": "operator",
        "sub": "staging-cli-subject",
        "iat": int(now),
        "exp": int(now) + 3600,
        "tenant_id": "codestra-platform",
        "scope": "platform.services.read observability.health.read observability.integrations.read",
        "realm_access": {"roles": ["platform_admin"]},
        "campaigns": ["campaign-one"],
        "services": ["middleware", "odoo"],
    }
    token = jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": KID})

    print(f"[jwks-stub] serving JWKS on 0.0.0.0:{port}/certs (issuer={ISSUER}, audience={AUDIENCE})")
    print(f"[jwks-stub] sample bearer token (expires in 1h):\n{token}")

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        thread.join()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
