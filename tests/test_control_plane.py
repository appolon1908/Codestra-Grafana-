import json
import unittest
from pathlib import Path
from urllib.request import urlopen

from codestra.control_plane.server import Catalog, Service, create_server



ROOT = Path(__file__).resolve().parents[1]


class ControlPlaneTests(unittest.TestCase):
    def test_catalog_is_loaded_without_modifying_source_registry(self):
        catalog = Catalog()
        self.assertIn("grafana", {service["id"] for service in catalog.list_services()})
        self.assertIn("klyrow-web", {service["id"] for service in catalog.list_services()})
        self.assertFalse(catalog.onboard("grafana")["production_activation"])

    def test_unknown_dependencies_fail_validation(self):
        catalog = Catalog()
        catalog._services["test-service"] = Service(
            id="test-service",
            name="Test service",
            type="application",
            owner="test",
            dependencies=["does-not-exist"],
            observability={
                "prometheus": False,
                "alloy": True,
                "loki": True,
                "tempo": True,
                "grafana": True,
                "alertmanager": True,
            },
        )
        result = catalog.onboard("test-service")
        self.assertFalse(result["prepared"])
        self.assertTrue(result["errors"])

    def test_configured_catalog_delegates_to_middleware(self):
        class FakeMiddleware:
            configured = True

            def repositories(self):
                return {"data": [{"repository": "Middleware-", "tenant": "platform"}]}

            def service_dependencies(self, service_id):
                return {"data": {"declared": ["postgres"]}}

            def service_coverage(self, service_id, environment):
                return {"data": {"status": "covered", "service_id": service_id}}

            def overview(self):
                return {"data": {"registered_services": 1}}

            def integrations(self, service_id):
                return {"data": {"service_id": service_id, "prometheus": "connected"}}

        catalog = Catalog(middleware=FakeMiddleware())
        self.assertEqual(catalog.list_services(), [{"repository": "Middleware-", "tenant": "platform"}])
        self.assertEqual(catalog.dependencies("middleware"), ["postgres"])
        self.assertEqual(catalog.status("middleware")["data"]["status"], "covered")
        self.assertEqual(catalog.platform_health()["data"]["registered_services"], 1)
        self.assertEqual(catalog.integration("middleware")["data"]["prometheus"], "connected")
    def test_http_health_and_service_listing(self):
        server = create_server(port=0)
        try:
            server_thread = __import__("threading").Thread(target=server.serve_forever, daemon=True)
            server_thread.start()
            base = f"http://127.0.0.1:{server.server_port}/platform/v1"
            with urlopen(f"{base}/observability/health") as response:
                self.assertEqual(response.status, 200)
                self.assertFalse(json.load(response)["production_activation"])
            with urlopen(f"{base}/services") as response:
                self.assertGreater(len(json.load(response)["services"]), 1)
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
