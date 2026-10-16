import http.client
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import web_ui


class LocalDashboardTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), web_ui.RequestHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(self, method, path, headers=None, body=None):
        client = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
        try:
            client.request(method, path, body=body, headers=headers or {})
            response = client.getresponse()
            return response.status, response.read()
        finally:
            client.close()

    def test_health_and_first_run_page(self):
        status, body = self.request("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertIn(b'"ok": true', body)
        status, body = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b'id="sendCodeBtn"', body)
        self.assertIn(b'id="completeLoginBtn"', body)
        self.assertEqual(self.request("GET", "/favicon.svg")[0], 200)

    def test_untrusted_host_cannot_read_account_state(self):
        with patch("web_ui.build_state_payload") as build:
            status, _ = self.request("GET", "/api/state", {"Host": "external.example"})
        self.assertEqual(status, 403)
        build.assert_not_called()

    def test_cross_origin_requests_cannot_change_configuration(self):
        with patch("local_runtime.save_env_values") as save:
            status, _ = self.request("POST", "/api/config/save", {
                "Origin": "https://external.example", "Content-Type": "application/json",
            }, b"{}")
        self.assertEqual(status, 403)
        save.assert_not_called()

    def test_same_origin_request_is_accepted(self):
        status, _ = self.request("GET", "/api/health", {
            "Origin": f"http://127.0.0.1:{self.server.server_port}",
            "Sec-Fetch-Site": "same-origin",
        })
        self.assertEqual(status, 200)

    def test_cross_site_fetch_without_origin_is_rejected(self):
        status, _ = self.request("GET", "/api/health", {"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(status, 403)


if __name__ == "__main__":
    unittest.main()
