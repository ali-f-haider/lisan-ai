import ast
import json
from pathlib import Path
import re
import shutil
import subprocess
import unittest

from api_errors import ERRORS
from api_schema import BODY_SCHEMAS

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs" / "api"


class ApiContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contract = json.loads((DOCS / "openapi.yaml").read_text(encoding="utf-8"))

    def test_version_bearer_security_and_versioned_paths(self):
        self.assertEqual(self.contract["openapi"], "3.1.0")
        self.assertEqual(self.contract["security"], [{"ApiKeyBearer": []}])
        self.assertEqual(self.contract["components"]["securitySchemes"]["ApiKeyBearer"]["scheme"], "bearer")
        self.assertTrue(all(path.startswith("/v1/") for path in self.contract["paths"]))

    def test_every_reference_resolves_locally(self):
        def walk(value):
            if isinstance(value, dict):
                if "$ref" in value:
                    self.assertTrue(value["$ref"].startswith("#/"))
                    node = self.contract
                    for part in value["$ref"][2:].split("/"):
                        node = node[part]
                for item in value.values():
                    walk(item)
            elif isinstance(value, list):
                for item in value:
                    walk(item)
        walk(self.contract)

    def test_all_request_bodies_share_tested_schemas_or_bounded_raw_chunk(self):
        used = set()
        for methods in self.contract["paths"].values():
            for entry in methods.values():
                body = entry.get("requestBody")
                if not body:
                    continue
                content = body["content"]
                if "application/json" in content:
                    name = content["application/json"]["schema"]["$ref"].rsplit("/", 1)[1]
                    used.add(name)
                    self.assertEqual(self.contract["components"]["schemas"][name], BODY_SCHEMAS[name])
                else:
                    schema = content["application/octet-stream"]["schema"]
                    self.assertEqual(schema["format"], "binary")
                    self.assertEqual(schema["maxLength"], 8388608)
        self.assertEqual(used, set(BODY_SCHEMAS))

    def test_every_mutation_requires_idempotency_and_every_operation_has_scope_and_error(self):
        names = set()
        for path, methods in self.contract["paths"].items():
            for method, entry in methods.items():
                self.assertNotIn(entry["operationId"], names)
                names.add(entry["operationId"])
                self.assertIn(entry["x-required-scope"], ("read", "dub", "account"))
                self.assertEqual(entry["responses"]["default"]["$ref"], "#/components/responses/PublicError")
                if method != "get":
                    keys = [p for p in entry["parameters"] if p["name"] == "Idempotency-Key"]
                    self.assertEqual(len(keys), 1, path)
                    self.assertTrue(keys[0]["required"])
                declared = {p["name"] for p in entry["parameters"] if p["in"] == "path"}
                self.assertEqual(declared, set(re.findall(r"{(.*?)}", path)))

    def test_paid_calls_require_quote_and_both_amounts(self):
        for suffix in ("/upload/finish", "/accept"):
            entry = self.contract["paths"]["/v1/jobs/{job_id}" + suffix]["post"]
            self.assertEqual(entry["requestBody"]["content"]["application/json"]["schema"]["$ref"], "#/components/schemas/AcceptQuote")
            self.assertIn("202", entry["responses"])
        self.assertTrue({"quote_id", "quoted_credits", "max_credits", "terms_version"}.issubset(BODY_SCHEMAS["AcceptQuote"]["required"]))

    def test_error_codes_and_documented_statuses_stay_in_sync(self):
        codes = self.contract["components"]["schemas"]["Error"]["properties"]["error"]["properties"]["code"]["enum"]
        self.assertEqual(set(codes), set(ERRORS))
        readme = (DOCS / "README.md").read_text(encoding="utf-8")
        for code, (status, _, _) in ERRORS.items():
            self.assertIn(f"| `{code}` | {status} |", readme)

    def test_public_docs_have_no_vendor_names_or_callback_inputs(self):
        forbidden = re.compile(r"elevenlabs|inworld|gemini|demucs|whisper|pyannote|fal-ai|openai|anthropic", re.I)
        for file in DOCS.iterdir():
            self.assertIsNone(forbidden.search(file.read_text(encoding="utf-8")), file.name)
        for schema in BODY_SCHEMAS.values():
            self.assertNotIn("callback_url", schema["properties"])

    def test_compact_upload_examples_are_exactly_fifteen_lines(self):
        text = (DOCS / "README.md").read_text(encoding="utf-8")
        blocks = re.findall(r"```(bash|python|javascript)\n(.*?)\n```", text, re.S)
        self.assertEqual(len(blocks), 3)
        for language, code in blocks:
            self.assertEqual(len(code.splitlines()), 15, language)

    def test_python_examples_compile_without_running_or_connecting(self):
        for name in ("README.md", "quickstart.md"):
            text = (DOCS / name).read_text(encoding="utf-8")
            blocks = re.findall(r"```python\n(.*?)\n```", text, re.S)
            self.assertEqual(len(blocks), 1)
            compile(blocks[0], name, "exec")

    def test_javascript_examples_parse_without_running_or_connecting(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("JavaScript syntax check requires the local Node.js executable")
        for name in ("README.md", "quickstart.md"):
            text = (DOCS / name).read_text(encoding="utf-8")
            blocks = re.findall(r"```javascript\n(.*?)\n```", text, re.S)
            self.assertEqual(len(blocks), 1)
            result = subprocess.run([node, "--check", "-"], input=blocks[0], text=True,
                                    capture_output=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_pure_modules_do_not_import_application_network_or_billing(self):
        allowed = {"copy", "datetime", "hashlib", "hmac", "json", "math", "re", "secrets"}
        for name in ("api_keys", "api_limits", "api_idempotency", "api_errors", "api_usage", "api_schema"):
            tree = ast.parse((ROOT / (name + ".py")).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    self.assertTrue(all(alias.name in allowed for alias in node.names), name)
                if isinstance(node, ast.ImportFrom):
                    self.assertIn(node.module, allowed, name)
