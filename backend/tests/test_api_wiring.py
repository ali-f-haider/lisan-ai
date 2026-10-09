"""The public API is connected to the app in the narrow, intended places only. Source-level checks (main.py is not imported)."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = (ROOT / "main.py").read_text(encoding="utf-8")
V1 = (ROOT / "api_v1.py").read_text(encoding="utf-8")
ADMIN = (ROOT / "admin.html").read_text(encoding="utf-8")
ACCOUNT = (ROOT / "account.html").read_text(encoding="utf-8")


class MainWiringTests(unittest.TestCase):
    def test_v1_skips_browser_login_but_after_the_site_gate(self):
        gate = MAIN.index("if not _site_gate_ok(request):")
        v1 = MAIN.index('if path.startswith("/v1/"):\n            return await call_next(request)')
        login = MAIN.index("if not _is_logged_in(request):")
        self.assertLess(gate, v1)
        self.assertLess(v1, login)

    def test_api_routes_are_included_with_the_error_handler(self):
        for needle in ("app.include_router(_api_router)", "app.include_router(_api_owner_router)",
                       "app.add_exception_handler(api_v1.ApiError, api_v1.handle_api_error)"):
            self.assertIn(needle, MAIN)

    def test_admin_endpoints_check_the_admin_token_first(self):
        for name in ("admin_get_api_settings", "admin_save_api_settings"):
            body = MAIN.split("def " + name, 1)[1][:260]
            self.assertIn("_admin_check(request)", body)

    def test_cannot_switch_on_without_the_server_secret(self):
        self.assertIn("pepper_from_env() is None", MAIN)
        self.assertIn("API_KEY_PEPPER", MAIN)

    def test_plan_gate_is_the_existing_long_dub_gate(self):
        self.assertIn("_ld_allowed(uid)[0]", MAIN.split("def _api_eligible", 1)[1][:600])

    def test_browser_login_untouched_for_other_paths(self):
        self.assertIn('if path in PUBLIC_PATHS or path.startswith("/api/auth/") or path.startswith("/api/admin/"):', MAIN)

    def test_unhandled_errors_on_v1_use_the_closed_error_body(self):
        self.assertIn('request.url.path.startswith("/v1/")', MAIN.split("async def _unhandled_error", 1)[1][:700])


class KeyCheckSourceTests(unittest.TestCase):
    def test_authentication_reads_only_the_authorization_header(self):
        auth = V1.split("def authenticate", 1)[1].split("def principal", 1)[0]
        for forbidden in ("cookies", "query_params", "request.json", "request.body", "request.form"):
            self.assertNotIn(forbidden, auth)
        self.assertIn('request.headers.get("authorization")', auth)

    def test_every_v1_route_uses_the_principal_dependency(self):
        routes = re.findall(r"@api\.(?:get|post|put|patch|delete)\([^\n]*\)\n\s*(?:async )?def (\w+)\(([^\n]*)\):", V1)
        self.assertTrue(routes)
        for name, args in routes:
            self.assertIn("principal(", args, name)

    def test_secret_never_written_to_logs(self):
        self.assertNotIn("print(", V1)


class PagesTests(unittest.TestCase):
    def test_admin_has_price_and_request_controls(self):
        for el in ("apiPrice", "apiRpm", "apiBurst", "apiConc", "apiCapDefault", "apiCapMax", "apiEnabled"):
            self.assertIn('id="%s"' % el, ADMIN)
        self.assertIn("/api/admin/api_settings", ADMIN)

    def test_account_tab_hidden_until_enabled_and_keys_use_textcontent(self):
        self.assertIn('id="tabApi" style="display:none;"', ACCOUNT)
        script = ACCOUNT.split("// API keys tab.", 1)[1].split("</script>", 1)[0]
        self.assertNotIn("innerHTML", script)


if __name__ == "__main__":
    unittest.main()
