"""Unit tests for curl boilerplate generation."""

import json
import os
import unittest

from restx.core import generate_curl, load_spec_from_file
from restx.core.curl_generator import (
    format_endpoint_choices,
    resolve_endpoint,
    resolve_endpoint_match,
)

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


def _load_curl_fixture():
    path = os.path.join(FIXTURES_DIR, "sample_curl.json")
    return load_spec_from_file(path)


def _load_petstore_fixture():
    path = os.path.join(FIXTURES_DIR, "sample_petstore_user.json")
    return load_spec_from_file(path)


def _endpoint(spec, method: str, path: str):
    for endpoint in spec.endpoints:
        if endpoint.method == method and endpoint.path == path:
            return endpoint
    raise AssertionError(f"Endpoint not found: {method} {path}")


def _extract_json_body(curl: str) -> object:
    start = curl.index("-d '") + len("-d '")
    end = curl.rindex("'")
    body_text = curl[start:end]
    return json.loads(body_text)


class TestCurlGenerator(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = _load_curl_fixture()
        cls.petstore_spec = _load_petstore_fixture()

    def test_url_with_path_and_query_placeholders(self):
        endpoint = _endpoint(self.spec, "GET", "/items/{id}")
        curl = generate_curl(endpoint, self.spec)
        self.assertIn("curl -X GET 'https://api.example.com/v1/items/{id}?filter={filter}' \\", curl)
        self.assertIn("-H 'Accept: application/json'", curl)
        self.assertNotIn("-d ", curl)

    def test_bearer_auth_from_global_security(self):
        endpoint = _endpoint(self.spec, "GET", "/items/{id}")
        curl = generate_curl(endpoint, self.spec)
        self.assertIn("Authorization: Bearer <YOUR_TOKEN_HERE>", curl)

    def test_api_key_auth(self):
        endpoint = _endpoint(self.spec, "POST", "/items")
        curl = generate_curl(endpoint, self.spec)
        self.assertIn("X-API-Key: <YOUR_API_KEY_HERE>", curl)
        self.assertIn("Content-Type: application/json", curl)

    def test_basic_auth(self):
        endpoint = _endpoint(self.spec, "GET", "/secure/basic")
        curl = generate_curl(endpoint, self.spec)
        self.assertIn("Authorization: Basic <USER:PASS_HERE>", curl)

    def test_oauth2_auth(self):
        endpoint = _endpoint(self.spec, "GET", "/secure/oauth")
        curl = generate_curl(endpoint, self.spec)
        self.assertIn("Authorization: Bearer <YOUR_OAUTH_TOKEN_HERE>", curl)

    def test_no_security_headers_when_operation_overrides_global(self):
        endpoint = _endpoint(self.spec, "GET", "/public")
        curl = generate_curl(endpoint, self.spec)
        self.assertNotIn("Authorization:", curl)
        self.assertNotIn("X-API-Key:", curl)

    def test_post_includes_content_type_when_body_present(self):
        endpoint = _endpoint(self.spec, "POST", "/items")
        curl = generate_curl(endpoint, self.spec)
        self.assertIn("Content-Type: application/json", curl)

    def test_get_without_body_omits_content_type(self):
        endpoint = _endpoint(self.spec, "GET", "/items/{id}")
        curl = generate_curl(endpoint, self.spec)
        self.assertNotIn("Content-Type", curl)

    def test_post_body_includes_typed_placeholders(self):
        endpoint = _endpoint(self.spec, "POST", "/items")
        curl = generate_curl(endpoint, self.spec)

        self.assertIn("-d '", curl)
        body = _extract_json_body(curl)
        self.assertEqual(body["name"], "<string>")

    def test_post_body_nested_object_placeholders(self):
        endpoint = _endpoint(self.spec, "POST", "/items")
        curl = generate_curl(endpoint, self.spec)

        body = _extract_json_body(curl)
        self.assertIn("address", body)
        self.assertEqual(body["address"]["street"], "<string>")
        self.assertEqual(body["address"]["city"], "<string>")
        self.assertEqual(body["address"]["zipCode"], "<integer>")

    def test_petstore_user_body_placeholders(self):
        endpoint = _endpoint(self.petstore_spec, "POST", "/user")
        curl = generate_curl(endpoint, self.petstore_spec)

        self.assertIn("-d '", curl)
        body = _extract_json_body(curl)
        self.assertEqual(body["id"], "<integer>")
        self.assertEqual(body["username"], "<string>")
        self.assertEqual(body["firstName"], "<string>")
        self.assertEqual(body["lastName"], "<string>")
        self.assertEqual(body["email"], "<string>")
        self.assertEqual(body["password"], "<string>")
        self.assertEqual(body["phone"], "<string>")
        self.assertEqual(body["userStatus"], "<integer>")

    def test_petstore_array_body_placeholders(self):
        endpoint = _endpoint(self.petstore_spec, "POST", "/user/createWithList")
        curl = generate_curl(endpoint, self.petstore_spec)

        body = _extract_json_body(curl)
        self.assertIsInstance(body, list)
        self.assertEqual(len(body), 1)
        user = body[0]
        self.assertEqual(user["username"], "<string>")
        self.assertEqual(user["userStatus"], "<integer>")

    def test_body_json_is_copy_pasteable_shell_command(self):
        endpoint = _endpoint(self.petstore_spec, "POST", "/user")
        curl = generate_curl(endpoint, self.petstore_spec)

        self.assertTrue(curl.startswith("curl -X POST "))
        self.assertIn("\\", curl)
        self.assertIn("-d '", curl)
        self.assertTrue(curl.rstrip().endswith("}'"))
        _extract_json_body(curl)


class TestEndpointResolution(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = _load_curl_fixture()
        cls.petstore_spec = _load_petstore_fixture()

    def test_resolve_single_exact_path(self):
        matches = resolve_endpoint("/items/{id}", self.spec)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].method, "GET")
        self.assertEqual(matches[0].path, "/items/{id}")

    def test_resolve_glob_multiple_matches(self):
        matches = resolve_endpoint("/items*", self.spec)
        paths = {(endpoint.method, endpoint.path) for endpoint in matches}
        self.assertEqual(
            paths,
            {("GET", "/items/{id}"), ("POST", "/items")},
        )

    def test_resolve_with_method_prefix(self):
        matches = resolve_endpoint("POST /items", self.spec)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].method, "POST")

    def test_resolve_no_match(self):
        matches = resolve_endpoint("/missing/*", self.spec)
        self.assertEqual(matches, [])

    def test_resolve_endpoint_match_unique(self):
        result = resolve_endpoint_match("GET /public", self.spec)
        self.assertTrue(result.is_unique)
        self.assertFalse(result.is_ambiguous)
        self.assertFalse(result.is_empty)

    def test_resolve_endpoint_match_ambiguous(self):
        result = resolve_endpoint_match("/items*", self.spec)
        self.assertTrue(result.is_ambiguous)
        self.assertFalse(result.is_unique)

    def test_format_endpoint_choices(self):
        matches = resolve_endpoint("/items*", self.spec)
        choices = format_endpoint_choices(matches)
        self.assertEqual(len(choices), 2)
        self.assertTrue(choices[0].startswith("1. "))
        self.assertIn("/items", choices[0])

    def test_petstore_user_glob(self):
        matches = resolve_endpoint("/user*", self.petstore_spec)
        paths = {endpoint.path for endpoint in matches}
        self.assertIn("/user", paths)
        self.assertIn("/user/createWithList", paths)


if __name__ == "__main__":
    unittest.main()
