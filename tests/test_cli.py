import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import openapi_diff as od


def _base_spec():
    return {
        "openapi": "3.0.3",
        "info": {"title": "Test", "version": "1.0.0"},
        "paths": {
            "/items": {
                "get": {
                    "parameters": [
                        {
                            "name": "filter",
                            "in": "query",
                            "required": False,
                            "schema": {"type": "string", "enum": ["a", "b", "c"]},
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "id": {"type": "integer"},
                                            "label": {"type": "string"},
                                        },
                                    }
                                }
                            },
                        }
                    },
                },
                "post": {
                    "requestBody": {
                        "required": False,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["title"],
                                    "properties": {
                                        "title": {"type": "string"},
                                        "code": {"type": "string"},
                                    },
                                }
                            }
                        },
                    },
                    "responses": {"201": {"description": "Created"}},
                },
            }
        },
    }


class TestOpenApiDiff(unittest.TestCase):
    def test_01_no_changes(self):
        s1 = _base_spec()
        s2 = _base_spec()
        self.assertEqual(od.diff_specs(s1, s2), [])

    def test_02_endpoint_removed(self):
        s1 = _base_spec()
        s2 = _base_spec()
        del s2["paths"]["/items"]
        changes = od.diff_specs(s1, s2)
        self.assertTrue(any(c.category == "endpoint_removed" and c.location == "/items" for c in changes))

    def test_03_method_removed(self):
        s1 = _base_spec()
        s2 = _base_spec()
        del s2["paths"]["/items"]["post"]
        changes = od.diff_specs(s1, s2)
        self.assertTrue(any(c.category == "method_removed" and "POST /items" in c.location for c in changes))

    def test_04_new_required_parameter(self):
        s1 = _base_spec()
        s2 = _base_spec()
        s2["paths"]["/items"]["get"]["parameters"].append(
            {"name": "tenant", "in": "header", "required": True, "schema": {"type": "string"}}
        )
        changes = od.diff_specs(s1, s2)
        self.assertTrue(any(c.category == "new_required_parameter" for c in changes))

    def test_05_new_optional_parameter_not_breaking(self):
        s1 = _base_spec()
        s2 = _base_spec()
        s2["paths"]["/items"]["get"]["parameters"].append(
            {"name": "opt", "in": "query", "required": False, "schema": {"type": "string"}}
        )
        self.assertEqual(od.diff_specs(s1, s2), [])

    def test_06_parameter_became_required(self):
        s1 = _base_spec()
        s2 = _base_spec()
        s2["paths"]["/items"]["get"]["parameters"][0]["required"] = True
        changes = od.diff_specs(s1, s2)
        self.assertTrue(any(c.category == "parameter_became_required" for c in changes))

    def test_07_enum_narrowed_in_parameter(self):
        s1 = _base_spec()
        s2 = _base_spec()
        s2["paths"]["/items"]["get"]["parameters"][0]["schema"]["enum"] = ["a", "b"]
        changes = od.diff_specs(s1, s2)
        self.assertTrue(any(c.category == "enum_narrowed" for c in changes))

    def test_08_enum_expanded_not_breaking(self):
        s1 = _base_spec()
        s2 = _base_spec()
        s2["paths"]["/items"]["get"]["parameters"][0]["schema"]["enum"] = ["a", "b", "c", "d"]
        self.assertEqual(od.diff_specs(s1, s2), [])

    def test_09_new_required_request_field(self):
        s1 = _base_spec()
        s2 = _base_spec()
        s2["paths"]["/items"]["post"]["requestBody"]["content"]["application/json"]["schema"]["required"].append("code")
        changes = od.diff_specs(s1, s2)
        self.assertTrue(any(c.category == "new_required_request_field" and "code" in c.location for c in changes))

    def test_10_request_body_became_required(self):
        s1 = _base_spec()
        s2 = _base_spec()
        s2["paths"]["/items"]["post"]["requestBody"]["required"] = True
        changes = od.diff_specs(s1, s2)
        self.assertTrue(any(c.category == "request_body_became_required" for c in changes))

    def test_11_response_type_changed(self):
        s1 = _base_spec()
        s2 = _base_spec()
        s2["paths"]["/items"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]["properties"]["id"]["type"] = "string"
        changes = od.diff_specs(s1, s2)
        self.assertTrue(any(c.category == "response_type_changed" for c in changes))

    def test_12_response_field_removed(self):
        s1 = _base_spec()
        s2 = _base_spec()
        del s2["paths"]["/items"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]["properties"]["label"]
        changes = od.diff_specs(s1, s2)
        self.assertTrue(any(c.category == "response_field_removed" for c in changes))

    def test_13_status_code_removed(self):
        s1 = _base_spec()
        s2 = _base_spec()
        del s2["paths"]["/items"]["post"]["responses"]["201"]
        s2["paths"]["/items"]["post"]["responses"]["200"] = {"description": "OK"}
        changes = od.diff_specs(s1, s2)
        self.assertTrue(any(c.category == "status_code_removed" and "201" in c.location for c in changes))

    def test_14_security_scheme_removed(self):
        s1 = _base_spec()
        s1["components"] = {"securitySchemes": {"bearerAuth": {"type": "http", "scheme": "bearer"}}}
        s2 = _base_spec()
        s2["components"] = {"securitySchemes": {}}
        changes = od.diff_specs(s1, s2)
        self.assertTrue(any(c.category == "security_scheme_removed" for c in changes))

    def test_15_security_scheme_changed(self):
        s1 = _base_spec()
        s1["components"] = {"securitySchemes": {"auth": {"type": "http", "scheme": "bearer"}}}
        s2 = _base_spec()
        s2["components"] = {"securitySchemes": {"auth": {"type": "apiKey", "scheme": "header"}}}
        changes = od.diff_specs(s1, s2)
        self.assertTrue(any(c.category == "security_scheme_changed" for c in changes))

    def test_16_security_strengthened(self):
        s1 = _base_spec()
        s2 = _base_spec()
        s2["security"] = [{"bearerAuth": []}]
        changes = od.diff_specs(s1, s2)
        self.assertTrue(any(c.category == "security_strengthened" and c.severity == "BREAKING" for c in changes))

    def test_17_security_weakened(self):
        s1 = _base_spec()
        s1["security"] = [{"bearerAuth": []}]
        s2 = _base_spec()
        changes = od.diff_specs(s1, s2)
        self.assertTrue(any(c.category == "security_weakened" and c.severity == "WARNING" for c in changes))

    def test_18_ref_resolution(self):
        s1 = _base_spec()
        s1["components"] = {"schemas": {"Item": {"type": "object", "properties": {"id": {"type": "integer"}}}}}
        s1["paths"]["/items"]["get"]["responses"]["200"]["content"]["application/json"]["schema"] = {"$ref": "#/components/schemas/Item"}
        s2 = _base_spec()
        s2["components"] = {"schemas": {"Item": {"type": "object", "properties": {"id": {"type": "string"}}}}}
        s2["paths"]["/items"]["get"]["responses"]["200"]["content"]["application/json"]["schema"] = {"$ref": "#/components/schemas/Item"}
        changes = od.diff_specs(s1, s2)
        self.assertTrue(any(c.category == "response_type_changed" for c in changes))

    def test_19_array_items_type_changed(self):
        s1 = _base_spec()
        s1["paths"]["/items"]["get"]["responses"]["200"]["content"]["application/json"]["schema"] = {
            "type": "array",
            "items": {"type": "integer"},
        }
        s2 = _base_spec()
        s2["paths"]["/items"]["get"]["responses"]["200"]["content"]["application/json"]["schema"] = {
            "type": "array",
            "items": {"type": "string"},
        }
        changes = od.diff_specs(s1, s2)
        self.assertTrue(any(c.category == "response_type_changed" for c in changes))

    def test_20_yaml_parser_basic(self):
        yaml_text = """
openapi: 3.0.3
info:
  title: Demo
  version: "1.0.0"
paths:
  /ping:
    get:
      responses:
        "200":
          description: pong
"""
        parsed = od.parse_yaml_lite(yaml_text)
        self.assertEqual(parsed["openapi"], "3.0.3")
        self.assertEqual(parsed["info"]["title"], "Demo")
        self.assertIn("/ping", parsed["paths"])

    def test_21_yaml_parser_lists_and_inline(self):
        yaml_text = """
required: [a, b]
parameters:
  - name: id
    in: path
    required: true
"""
        parsed = od.parse_yaml_lite(yaml_text)
        self.assertEqual(parsed["required"], ["a", "b"])
        self.assertEqual(parsed["parameters"][0]["name"], "id")
        self.assertTrue(parsed["parameters"][0]["required"])

    def test_22_format_text_empty(self):
        self.assertEqual(od.format_text([]), "No breaking changes detected.")

    def test_23_format_text_non_empty(self):
        c = od.BreakingChange("endpoint_removed", "BREAKING", "/x", "Removed")
        out = od.format_text([c])
        self.assertIn("[BREAKING]", out)
        self.assertIn("/x", out)

    def test_24_format_json(self):
        c = od.BreakingChange("endpoint_removed", "BREAKING", "/x", "Removed")
        data = json.loads(od.format_json([c]))
        self.assertEqual(data["breaking_count"], 1)
        self.assertEqual(data["total_count"], 1)

    def test_25_format_markdown(self):
        c = od.BreakingChange("endpoint_removed", "BREAKING", "/x", "Removed")
        md = od.format_markdown([c])
        self.assertIn("| Severity | Category | Location | Message |", md)
        self.assertIn("`endpoint_removed`", md)

    def test_26_cli_exit_code_1_on_breaking(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = od.main(["sample_specs/v1.json", "sample_specs/v2.json", "--format", "json"])
        self.assertEqual(code, 1)
        payload = json.loads(buf.getvalue())
        self.assertGreater(payload["breaking_count"], 0)

    def test_27_cli_exit_code_0_on_identical(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = od.main(["sample_specs/v1.json", "sample_specs/v1.json"])
        self.assertEqual(code, 0)
        self.assertIn("No breaking changes detected.", buf.getvalue())

    def test_28_cli_yaml_sample_specs(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = od.main(["sample_specs/v1.yaml", "sample_specs/v2.yaml", "--format", "markdown"])
        self.assertEqual(code, 1)
        self.assertIn("method_removed", buf.getvalue())

    def test_29_load_spec_invalid_root_raises(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "bad.json"
            p.write_text("[1, 2, 3]", encoding="utf-8")
            with self.assertRaises(ValueError):
                od.load_spec(p)


if __name__ == "__main__":
    unittest.main()
