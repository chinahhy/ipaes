import base64
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "rootfs" / "app"))

try:
    import flask  # noqa: F401
    import webui
except ImportError:  # local machines without the container's Flask dependency
    webui = None


AUTH = {
    "Authorization": "Basic "
    + base64.b64encode(b"admin:admin").decode("ascii")
}


@unittest.skipIf(webui is None, "flask is not installed")
class WebuiRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = webui.app.test_client()

    def test_login_page_and_public_icons_do_not_require_a_session(self):
        self.assertEqual(self.client.get("/login").status_code, 200)
        for icon in ("favicon.ico", "icon-192.png", "icon-512.png"):
            self.assertEqual(self.client.get(f"/static/{icon}").status_code, 200)

    def test_private_pages_redirect_and_apis_return_401(self):
        response = self.client.get("/apps")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/login")
        for path in ("/api/status", "/api/bot", "/api/logs/tg-cron"):
            self.assertEqual(self.client.get(path).status_code, 401)

    def test_status_exposes_scan_health_fields(self):
        response = self.client.get("/api/status", headers=AUTH)
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        for key in (
            "last_scan_status",
            "last_scan_errors",
            "last_scan_age_seconds",
            "last_scan_error",
            "session_present",
            "scan_stale",
        ):
            self.assertIn(key, data)

    def test_bot_endpoint_never_returns_the_full_token(self):
        response = self.client.get("/api/bot", headers=AUTH)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["token"], "")

    def test_github_repo_management_preserves_other_config(self):
        from unittest.mock import patch
        import json
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / "config.json"
            config.write_text(json.dumps({"groups": ["https://t.me/example"], "whitelist": [], "secret": "keep"}))
            with patch.object(webui, "CONFIG_PATH", config):
                self.assertEqual(self.client.post("/api/github/repos", json={"url": "a/b"}).status_code, 401)
                self.assertEqual(self.client.post("/api/github/repos", headers=AUTH, json={"url": "https://evil.test/a/b"}).status_code, 400)
                response = self.client.post("/api/github/repos", headers=AUTH, json={"url": "https://github.com/Owner/Repo"})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(self.client.post("/api/github/repos", headers=AUTH, json={"url": "owner/repo"}).status_code, 409)
                self.assertEqual(self.client.get("/api/config", headers=AUTH).get_json()["github_repos"], ["owner/repo"])
                self.assertEqual(self.client.delete("/api/github/repos", headers=AUTH, json={"url": "owner/repo"}).status_code, 200)
                saved = json.loads(config.read_text())
                self.assertEqual(saved["groups"], ["https://t.me/example"])
                self.assertEqual(saved["secret"], "keep")
                self.assertEqual(saved["github_repos"], [])

    def test_removed_dead_routes_are_gone(self):
        rules = {rule.rule for rule in webui.app.url_map.iter_rules()}
        self.assertNotIn("/api/restart", rules)
        self.assertNotIn("/api/password", rules)
        self.assertNotIn("/static/icons/<path:p>", rules)

    def test_log_tail_reads_only_the_requested_suffix(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            path = Path(temp) / "scan.log"
            path.write_text("".join(f"line-{n}\n" for n in range(500)), encoding="utf-8")
            lines = webui._tail_lines(path, 5)
            self.assertEqual(lines, [f"line-{n}\n" for n in range(495, 500)])


if __name__ == "__main__":
    unittest.main()
