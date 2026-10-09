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


class DubWiringTests(unittest.TestCase):
    """The paid endpoints reach money only through the website's own route functions."""
    def adapter(self):
        return MAIN.split("class _ApiLongDub", 1)[1].split("_api_deps = api_v1.Deps(", 1)[0]

    def test_adapter_calls_the_website_routes(self):
        a = self.adapter()
        for fn in ("longdub_init", "longdub_chunk", "longdub_finish", "longdub_accept", "longdub_preview", "longdub_confirm", "longdub_delete"):
            self.assertIn(fn, a)
        self.assertIn("LongDubAccept(agree=True)", a)
        self.assertIn("longdub_service._job_pricing(job)", a)

    def test_every_method_the_api_uses_exists(self):
        a = self.adapter()
        api_dub = (ROOT / "api_dub.py").read_text(encoding="utf-8")
        used = set(re.findall(r"\bld\.(\w+)\(", api_dub))
        self.assertTrue(used)
        for name in used:
            self.assertRegex(a, r"def %s\(" % name)

    def test_api_module_never_moves_money_itself(self):
        api_dub = (ROOT / "api_dub.py").read_text(encoding="utf-8")
        for bad in ("Hooks", "_charge", "charge(", "refund(", "credit_billing", "rpc"):
            self.assertNotIn(bad, api_dub)

    def test_deps_get_delete_and_the_adapter(self):
        block = MAIN.split("_api_deps = api_v1.Deps(", 1)[1][:900]
        self.assertIn("delete=", block)
        self.assertIn("ld=_ApiLongDub()", block)

    def test_current_uid_trusts_only_the_principal_set_by_api_v1(self):
        head = MAIN.split("def _current_uid(request: Request):", 1)[1][:700]
        self.assertIn('getattr(request.state, "api_principal", None)', head)
        self.assertLess(head.index("api_principal"), head.index('request.cookies.get("session"'))
        setters = [m.start() for m in re.finditer(r"api_principal\s*=", MAIN)]
        self.assertEqual(setters, [])                      # main.py never sets it; only api_v1.authenticate does
        self.assertEqual(len(re.findall(r"request\.state\.api_principal\s*=", V1)), 1)

    def test_unique_key_conflict_is_reported_as_conflict(self):
        ins = MAIN.split("def _api_insert", 1)[1][:500]
        self.assertIn("ex.code == 409", ins)
        self.assertIn("api_core.Conflict()", ins)

    def test_blocking_work_does_not_run_on_the_event_loop(self):
        a = self.adapter()
        for fn in ("longdub_init", "longdub_finish", "longdub_accept", "longdub_preview", "longdub_confirm", "longdub_delete"):
            self.assertIn("run_in_threadpool(%s" % fn, a)


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


class GuidePageTests(unittest.TestCase):
    """The public API guide (api_docs.html) matches the code it describes."""
    PAGE = (ROOT / "api_docs.html").read_text(encoding="utf-8")

    def test_page_is_served_publicly(self):
        self.assertIn('"/api-docs"', MAIN.split("PUBLIC_PATHS = frozenset([", 1)[1][:1200])
        self.assertIn('@app.get("/api-docs")', MAIN)
        self.assertIn('href="/api-docs"', ACCOUNT)

    def test_every_live_call_is_listed(self):
        import html
        src = (ROOT / "api_dub.py").read_text(encoding="utf-8") + V1
        calls = re.findall(r'@(?:api|owner)\.(get|post|put|delete)\("(/[^"]*)"', (ROOT / "api_dub.py").read_text(encoding="utf-8"))
        calls.append(("get", "/account/balance"))
        self.assertGreaterEqual(len(calls), 12)
        for method, path in calls:
            path = re.sub(r"\{(job_id)\}", "{id}", path)
            self.assertIn("%s /v1%s" % (method.upper(), path), self.PAGE, (method, path))

    def test_every_error_code_is_listed(self):
        import sys
        sys.path.insert(0, str(ROOT))
        import api_errors
        for code, (status, retry, message) in api_errors.ERRORS.items():
            self.assertIn("<code>%s</code>" % code, self.PAGE)
            self.assertIn("<td>%d</td><td>%s</td>" % (status, "yes" if retry else "no"), self.PAGE)

    def test_the_embedded_tool_is_the_file_and_the_terms_version_is_current(self):
        import html
        tool = (ROOT / "api_try.py").read_text(encoding="utf-8").replace("\r\n", "\n")
        self.assertIn(html.escape(tool), self.PAGE)
        self.assertIn('TERMS = "%s"' % re.search(r'TERMS_VERSION = "([^"]+)"', V1).group(1), tool)
        self.assertIn('"terms_version": "%s"' % re.search(r'TERMS_VERSION = "([^"]+)"', V1).group(1), self.PAGE)

    def test_no_vendor_names_in_the_guide(self):
        low = self.PAGE.lower()
        for name in ("gemini", "elevenlabs", "inworld", "openai", "chatgpt", "claude", "anthropic", "fal.ai", "pyannote", "whisper"):
            self.assertNotIn(name, low)
