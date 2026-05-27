"""
Unit tests for spec loading and parsing.
"""

import io
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

from restx.core import (
    SpecLoadError,
    SpecParseError,
    UnsupportedSpecVersionError,
    detect_auth_type,
    format_loaded_spec_message,
    load_spec,
    load_spec_from_file,
    load_spec_from_stdin,
    load_spec_from_url,
    parse_spec_text,
)
from restx.core.spec_loader import AUTH_TYPE_API_KEY, AUTH_TYPE_BASIC, AUTH_TYPE_BEARER, AUTH_TYPE_MIXED, AUTH_TYPE_NONE
from restx.core.models import ParsedSpec, SecurityScheme

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


class TestSpecLoader(unittest.TestCase):
    def test_valid_openapi_30_json(self):
        path = os.path.join(FIXTURES_DIR, "sample_openapi_30.json")
        spec = load_spec_from_file(path)

        self.assertEqual(spec.title, "Pet Store API")
        self.assertEqual(spec.version, "1.0.0")
        self.assertEqual(spec.openapi_version, "3.0.3")
        self.assertEqual(spec.endpoint_count, 3)
        self.assertEqual(spec.servers, ["https://api.example.com/v1"])
        self.assertIn("bearerAuth", spec.security_schemes)
        self.assertIn("apiKeyAuth", spec.security_schemes)

        get_users = next(
            endpoint
            for endpoint in spec.endpoints
            if endpoint.method == "GET" and endpoint.path == "/users"
        )
        self.assertEqual(get_users.summary, "List users")
        self.assertEqual(len(get_users.parameters), 1)
        self.assertEqual(get_users.parameters[0].name, "email")
        self.assertEqual(get_users.parameters[0].location, "query")
        self.assertEqual(len(get_users.responses), 1)
        self.assertEqual(get_users.responses[0].status_code, "200")

        get_user_by_id = next(
            endpoint
            for endpoint in spec.endpoints
            if endpoint.method == "GET" and endpoint.path == "/users/{id}"
        )
        self.assertEqual(len(get_user_by_id.parameters), 2)
        path_param = next(
            parameter for parameter in get_user_by_id.parameters if parameter.location == "path"
        )
        self.assertEqual(path_param.name, "id")
        self.assertTrue(path_param.required)

        post_users = next(
            endpoint
            for endpoint in spec.endpoints
            if endpoint.method == "POST" and endpoint.path == "/users"
        )
        self.assertEqual(len(post_users.parameters), 1)
        self.assertEqual(post_users.parameters[0].location, "body")
        self.assertEqual(post_users.security, [{"apiKeyAuth": []}])

    def test_valid_openapi_30_yaml(self):
        path = os.path.join(FIXTURES_DIR, "sample_openapi_30.yaml")
        spec = load_spec_from_file(path)

        self.assertEqual(spec.openapi_version, "3.0.3")
        self.assertEqual(spec.endpoint_count, 3)
        self.assertEqual(spec.title, "Pet Store API")

    def test_valid_swagger_20_json(self):
        path = os.path.join(FIXTURES_DIR, "sample_swagger_2.json")
        spec = load_spec_from_file(path)

        self.assertEqual(spec.openapi_version, "2.0")
        self.assertEqual(spec.title, "Sample API")
        self.assertEqual(spec.endpoint_count, 1)

        get_users = spec.endpoints[0]
        self.assertEqual(get_users.method, "GET")
        self.assertEqual(get_users.path, "/users")
        self.assertEqual(get_users.summary, "List users")

    def test_valid_swagger_20_yaml(self):
        path = os.path.join(FIXTURES_DIR, "sample_swagger_20.yaml")
        spec = load_spec_from_file(path)

        self.assertEqual(spec.openapi_version, "2.0")
        self.assertEqual(spec.endpoint_count, 3)
        self.assertEqual(spec.servers, ["https://api.example.com/v1"])
        self.assertIn("apiKeyAuth", spec.security_schemes)

        get_users = next(
            endpoint
            for endpoint in spec.endpoints
            if endpoint.method == "GET" and endpoint.path == "/users"
        )
        self.assertEqual(get_users.parameters[0].name, "email")
        self.assertEqual(get_users.parameters[0].schema, {"type": "string"})

        post_users = next(
            endpoint
            for endpoint in spec.endpoints
            if endpoint.method == "POST" and endpoint.path == "/users"
        )
        self.assertEqual(post_users.parameters[0].location, "body")
        self.assertEqual(post_users.security, [{"apiKeyAuth": []}])

    def test_valid_openapi_31_json(self):
        path = os.path.join(FIXTURES_DIR, "sample_openapi_31.json")
        spec = load_spec_from_file(path)

        self.assertEqual(spec.openapi_version, "3.1.0")
        self.assertEqual(spec.endpoint_count, 1)

    def test_valid_openapi_31_yaml(self):
        path = os.path.join(FIXTURES_DIR, "sample_openapi_31.yaml")
        spec = load_spec_from_file(path)

        self.assertEqual(spec.openapi_version, "3.1.2")
        self.assertEqual(spec.endpoint_count, 1)

    def test_valid_openapi_32_json(self):
        path = os.path.join(FIXTURES_DIR, "sample_openapi_32.json")
        spec = load_spec_from_file(path)

        self.assertEqual(spec.openapi_version, "3.2.0")
        self.assertEqual(spec.endpoint_count, 1)

    def test_valid_openapi_32_yaml(self):
        path = os.path.join(FIXTURES_DIR, "sample_openapi_32.yaml")
        spec = load_spec_from_file(path)

        self.assertEqual(spec.openapi_version, "3.2.0")
        self.assertEqual(spec.endpoint_count, 1)

    def test_yaml_file_with_json_content(self):
        """Format detection uses content, not extension."""
        path = os.path.join(FIXTURES_DIR, "sample_openapi_30.json")
        raw_text = open(path, encoding="utf-8").read()
        spec = parse_spec_text(raw_text, source_label="spec.yaml")

        self.assertEqual(spec.openapi_version, "3.0.3")
        self.assertEqual(spec.endpoint_count, 3)

    def test_malformed_json(self):
        path = os.path.join(FIXTURES_DIR, "sample_malformed.json")
        with self.assertRaises(SpecParseError) as ctx:
            load_spec_from_file(path)

        message = str(ctx.exception)
        self.assertIn("Failed to parse spec", message)
        self.assertIn("line", message.lower())
        self.assertIn("Verify the file is valid JSON", message)

    def test_malformed_yaml(self):
        path = os.path.join(FIXTURES_DIR, "sample_malformed.yaml")
        with self.assertRaises(SpecParseError) as ctx:
            load_spec_from_file(path)

        message = str(ctx.exception)
        self.assertIn("Failed to parse spec", message)
        self.assertIn("YAML error at line", message)
        self.assertIn("Verify the file is valid YAML", message)

    def test_missing_file(self):
        path = os.path.join(FIXTURES_DIR, "does-not-exist.json")
        with self.assertRaises(SpecLoadError) as ctx:
            load_spec_from_file(path)

        self.assertIn("File not found", str(ctx.exception))
        self.assertIn(path, str(ctx.exception))

    def test_unsupported_version(self):
        path = os.path.join(FIXTURES_DIR, "sample_swagger_12.json")
        with self.assertRaises(UnsupportedSpecVersionError) as ctx:
            load_spec_from_file(path)

        self.assertEqual(
            str(ctx.exception),
            "Unsupported spec version: 1.2. RestX supports Swagger 2.0 and OpenAPI 3.0.x–3.2.x.",
        )


class TestSchemaExtraction(unittest.TestCase):
    def test_petstore_user_request_body_schema(self):
        path = os.path.join(FIXTURES_DIR, "sample_petstore_user.json")
        spec = load_spec_from_file(path)

        post_user = next(
            endpoint
            for endpoint in spec.endpoints
            if endpoint.method == "POST" and endpoint.path == "/user"
        )
        body_param = next(
            parameter for parameter in post_user.parameters if parameter.location == "body"
        )

        self.assertEqual(body_param.name, "body")
        self.assertTrue(body_param.required)
        self.assertIsNotNone(body_param.schema)
        self.assertIn("$ref", body_param.schema)

        schema_node = body_param.schema_node
        self.assertIsNotNone(schema_node)
        assert schema_node is not None
        self.assertEqual(schema_node.type, "object")
        self.assertEqual(schema_node.ref_name, "User")
        self.assertEqual(len(schema_node.children), 8)

        children_by_name = {child.name: child for child in schema_node.children}
        self.assertIn("id", children_by_name)
        self.assertEqual(children_by_name["id"].type, "integer")
        self.assertEqual(children_by_name["id"].format, "int64")
        self.assertFalse(children_by_name["id"].required)

        self.assertIn("username", children_by_name)
        self.assertEqual(children_by_name["username"].type, "string")
        self.assertTrue(children_by_name["username"].required)

        self.assertIn("firstName", children_by_name)
        self.assertEqual(children_by_name["firstName"].type, "string")

        self.assertIn("userStatus", children_by_name)
        self.assertEqual(children_by_name["userStatus"].type, "integer")
        self.assertEqual(children_by_name["userStatus"].format, "int32")
        self.assertEqual(children_by_name["userStatus"].description, "User Status")

    def test_petstore_user_response_schema(self):
        path = os.path.join(FIXTURES_DIR, "sample_petstore_user.json")
        spec = load_spec_from_file(path)

        get_user = next(
            endpoint
            for endpoint in spec.endpoints
            if endpoint.method == "GET" and endpoint.path == "/user"
        )
        response = next(
            response for response in get_user.responses if response.status_code == "200"
        )

        schema_node = response.schema_node
        self.assertIsNotNone(schema_node)
        assert schema_node is not None
        self.assertEqual(schema_node.type, "object")
        self.assertEqual(schema_node.ref_name, "User")
        self.assertEqual(len(schema_node.children), 8)

    def test_petstore_array_request_body_items(self):
        path = os.path.join(FIXTURES_DIR, "sample_petstore_user.json")
        spec = load_spec_from_file(path)

        post_list = next(
            endpoint
            for endpoint in spec.endpoints
            if endpoint.method == "POST" and endpoint.path == "/user/createWithList"
        )
        body_param = next(
            parameter for parameter in post_list.parameters if parameter.location == "body"
        )

        schema_node = body_param.schema_node
        self.assertIsNotNone(schema_node)
        assert schema_node is not None
        self.assertEqual(schema_node.type, "array")
        self.assertEqual(len(schema_node.children), 1)

        item_node = schema_node.children[0]
        self.assertEqual(item_node.type, "object")
        self.assertEqual(item_node.ref_name, "User")
        self.assertEqual(len(item_node.children), 8)

    def test_openapi_30_request_body_schema_node(self):
        path = os.path.join(FIXTURES_DIR, "sample_openapi_30.json")
        spec = load_spec_from_file(path)

        post_users = next(
            endpoint
            for endpoint in spec.endpoints
            if endpoint.method == "POST" and endpoint.path == "/users"
        )
        body_param = post_users.parameters[0]

        self.assertEqual(body_param.location, "body")
        self.assertIsNotNone(body_param.schema)
        self.assertIn("$ref", body_param.schema)
        # User is referenced but not defined in this fixture; node captures the ref.
        self.assertIsNotNone(body_param.schema_node)
        assert body_param.schema_node is not None
        self.assertEqual(body_param.schema_node.ref_name, "User")


class TestSpecInputMethods(unittest.TestCase):
    def test_load_spec_from_url(self):
        path = os.path.join(FIXTURES_DIR, "sample_openapi_30.json")
        raw_text = open(path, encoding="utf-8").read()
        mock_response = MagicMock()
        mock_response.text = raw_text
        mock_response.raise_for_status = MagicMock()

        with patch("restx.core.spec_loader.requests.get", return_value=mock_response):
            spec = load_spec_from_url("https://example.com/spec.json")

        self.assertEqual(spec.openapi_version, "3.0.3")
        self.assertEqual(spec.endpoint_count, 3)

    def test_load_spec_url_detection(self):
        path = os.path.join(FIXTURES_DIR, "sample_openapi_30.json")
        with patch(
            "restx.core.spec_loader.load_spec_from_url",
            return_value=load_spec_from_file(path),
        ) as mock_url:
            spec = load_spec("https://example.com/spec.json")

        mock_url.assert_called_once_with(
            "https://example.com/spec.json",
            spinner=None,
        )
        self.assertEqual(spec.openapi_version, "3.0.3")

    def test_load_spec_file_detection(self):
        path = os.path.join(FIXTURES_DIR, "sample_openapi_30.json")
        spec = load_spec(path)
        self.assertEqual(spec.openapi_version, "3.0.3")

    def test_url_fetch_timeout(self):
        with patch(
            "restx.core.spec_loader.requests.get",
            side_effect=__import__("requests").exceptions.Timeout(),
        ):
            with self.assertRaises(SpecLoadError) as ctx:
                load_spec_from_url("https://example.com/spec.json")

        message = str(ctx.exception)
        self.assertIn("Failed to fetch 'https://example.com/spec.json'", message)
        self.assertIn("Connection timed out", message)
        self.assertIn("Verify the URL is accessible", message)

    def test_url_fetch_http_error(self):
        response = MagicMock()
        response.status_code = 404
        http_error = __import__("requests").exceptions.HTTPError(response=response)

        with patch(
            "restx.core.spec_loader.requests.get",
            return_value=response,
        ):
            response.raise_for_status.side_effect = http_error
            with self.assertRaises(SpecLoadError) as ctx:
                load_spec_from_url("https://example.com/spec.json")

        message = str(ctx.exception)
        self.assertIn("Failed to fetch 'https://example.com/spec.json'", message)
        self.assertIn("HTTP 404", message)
        self.assertIn("Verify the URL is accessible", message)

    def test_url_fetch_connection_error(self):
        with patch(
            "restx.core.spec_loader.requests.get",
            side_effect=__import__("requests").exceptions.ConnectionError(
                "HTTPSConnectionPool(host='bad.example', port=443): "
                "Max retries exceeded with url: /spec.json "
                "(Caused by NameResolutionError: Failed to resolve 'bad.example')"
            ),
        ):
            with self.assertRaises(SpecLoadError) as ctx:
                load_spec_from_url("https://bad.example/spec.json")

        message = str(ctx.exception)
        self.assertIn("Failed to fetch 'https://bad.example/spec.json'", message)
        self.assertIn("Verify the URL is accessible", message)

    def test_load_spec_from_stdin(self):
        path = os.path.join(FIXTURES_DIR, "sample_openapi_30.json")
        raw_text = open(path, encoding="utf-8").read()

        with patch.object(sys, "stdin", io.StringIO(raw_text)):
            spec = load_spec_from_stdin()

        self.assertEqual(spec.openapi_version, "3.0.3")

    def test_empty_stdin(self):
        with patch.object(sys, "stdin", io.StringIO("")):
            with self.assertRaises(SpecLoadError) as ctx:
                load_spec_from_stdin()

        self.assertEqual(
            str(ctx.exception),
            "No input received on stdin. Pipe a spec file: cat spec.json | restx",
        )

    def test_load_spec_stdin_when_no_argument(self):
        path = os.path.join(FIXTURES_DIR, "sample_openapi_30.yaml")
        raw_text = open(path, encoding="utf-8").read()

        with patch.object(sys, "stdin", io.StringIO(raw_text)):
            with patch.object(sys.stdin, "isatty", return_value=False):
                spec = load_spec(None)

        self.assertEqual(spec.openapi_version, "3.0.3")

    def test_load_spec_empty_stdin_when_tty(self):
        with patch.object(sys.stdin, "isatty", return_value=True):
            with self.assertRaises(SpecLoadError) as ctx:
                load_spec(None)

        self.assertEqual(
            str(ctx.exception),
            "No input received on stdin. Pipe a spec file: cat spec.json | restx",
        )


class TestSpecFeedback(unittest.TestCase):
    def test_detect_auth_type_mixed(self):
        path = os.path.join(FIXTURES_DIR, "sample_openapi_30.json")
        spec = load_spec_from_file(path)
        self.assertEqual(detect_auth_type(spec), AUTH_TYPE_MIXED)

    def test_detect_auth_type_api_key(self):
        path = os.path.join(FIXTURES_DIR, "sample_swagger_20.yaml")
        spec = load_spec_from_file(path)
        self.assertEqual(detect_auth_type(spec), AUTH_TYPE_API_KEY)

    def test_detect_auth_type_none(self):
        path = os.path.join(FIXTURES_DIR, "sample_swagger_2.json")
        spec = load_spec_from_file(path)
        self.assertEqual(detect_auth_type(spec), AUTH_TYPE_NONE)

    def test_detect_auth_type_bearer(self):
        spec = ParsedSpec(
            title="Test API",
            version="1.0.0",
            openapi_version="3.0.3",
            security_schemes={
                "bearerAuth": SecurityScheme(
                    name="bearerAuth",
                    type="http",
                    scheme="bearer",
                )
            },
        )
        self.assertEqual(detect_auth_type(spec), AUTH_TYPE_BEARER)

    def test_detect_auth_type_basic(self):
        spec = ParsedSpec(
            title="Test API",
            version="1.0.0",
            openapi_version="3.0.3",
            security_schemes={
                "basicAuth": SecurityScheme(
                    name="basicAuth",
                    type="http",
                    scheme="basic",
                )
            },
        )
        self.assertEqual(detect_auth_type(spec), AUTH_TYPE_BASIC)

    def test_format_loaded_spec_message_includes_auth_and_servers(self):
        path = os.path.join(FIXTURES_DIR, "sample_openapi_30.json")
        spec = load_spec_from_file(path)
        message = format_loaded_spec_message(spec)

        self.assertIn("Loaded spec: Pet Store API v1.0.0 (3.0.3)", message)
        self.assertIn("3 endpoints", message)
        self.assertIn("1 server", message)
        self.assertIn("Auth: Mixed (", message)
        self.assertIn("Bearer", message)
        self.assertIn("API Key", message)

    def test_format_loaded_spec_message_no_auth(self):
        path = os.path.join(FIXTURES_DIR, "sample_dsl.json")
        spec = load_spec_from_file(path)
        message = format_loaded_spec_message(spec)

        self.assertIn("Auth: None", message)
        self.assertIn("0 servers", message)


if __name__ == "__main__":
    unittest.main()
