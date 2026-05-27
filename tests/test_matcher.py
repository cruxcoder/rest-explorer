"""Unit tests for glob/regex matching and endpoint query evaluation."""

import os
import unittest

from restx.core import execute_query, load_spec_from_file
from restx.core.dsl_parser import parse_query
from restx.core.matcher import (
    ZERO_MATCH_MESSAGE,
    filter_endpoints,
    format_match_results,
    path_glob_match,
)
from restx.core.spec_loader import parse_spec_document

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


def _load_dsl_fixture():
    path = os.path.join(FIXTURES_DIR, "sample_dsl.json")
    return load_spec_from_file(path)


class TestPathGlobMatch(unittest.TestCase):
    def test_literal_path_param_segment(self):
        self.assertTrue(path_glob_match("/user/{name}", "/user/{name}"))

    def test_glob_matches_path_param_segment(self):
        self.assertTrue(path_glob_match("/user/*", "/user/{name}"))

    def test_different_path_param_names_do_not_match(self):
        self.assertFalse(path_glob_match("/user/{id}", "/user/{name}"))

    def test_deep_glob(self):
        self.assertTrue(path_glob_match("/user/**", "/user/{name}/posts"))


class TestBasicQueries(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = _load_dsl_fixture()

    def test_bare_method(self):
        matches = execute_query("GET", self.spec)
        methods = {endpoint.method for endpoint in matches}
        self.assertEqual(methods, {"GET"})
        self.assertGreaterEqual(len(matches), 4)

    def test_post_users_glob(self):
        matches = execute_query("POST /users*", self.spec)
        paths = {endpoint.path for endpoint in matches}
        self.assertEqual(paths, {"/users", "/users/bulk"})

    def test_path_glob_any_method(self):
        matches = execute_query("/users*", self.spec)
        self.assertTrue(all(endpoint.path.startswith("/users") for endpoint in matches))
        self.assertIn("GET", {endpoint.method for endpoint in matches})
        self.assertIn("POST", {endpoint.method for endpoint in matches})

    def test_implicit_and_same_as_explicit(self):
        implicit = execute_query("GET /users*", self.spec)
        explicit = execute_query("GET && /users*", self.spec)
        self.assertEqual(
            {(m.method, m.path) for m in implicit},
            {(m.method, m.path) for m in explicit},
        )

    def test_zero_matches_message(self):
        matches = execute_query("PATCH /nope*", self.spec)
        output = format_match_results(matches)
        self.assertEqual(output, ZERO_MATCH_MESSAGE)

    def test_numbered_match_output(self):
        matches = execute_query("GET /users", self.spec)
        output = format_match_results(matches)
        self.assertIn("1 match", output)
        self.assertIn("[1] GET", output)
        self.assertIn("params:", output)
        self.assertIn("email", output)


class TestFullDslQueries(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = _load_dsl_fixture()

    def test_req_email(self):
        matches = execute_query("req:email", self.spec)
        paths = {(m.method, m.path) for m in matches}
        self.assertIn(("GET", "/users"), paths)
        self.assertIn(("GET", "/users/{id}"), paths)
        self.assertNotIn(("DELETE", "/users/{id}"), paths)

    def test_resp_owner_glob(self):
        matches = execute_query("resp:owner*", self.spec)
        paths = {endpoint.path for endpoint in matches}
        self.assertIn("/dogs", paths)
        self.assertIn("/dogs/{id}", paths)

    def test_reqpath_id(self):
        matches = execute_query("reqpath:id", self.spec)
        paths = {(m.method, m.path) for m in matches}
        self.assertIn(("GET", "/users/{id}"), paths)
        self.assertIn(("PUT", "/users/{id}"), paths)
        self.assertNotIn(("GET", "/users"), paths)

    def test_method_not_equal(self):
        matches = execute_query("method != DELETE", self.spec)
        self.assertTrue(all(endpoint.method != "DELETE" for endpoint in matches))
        self.assertGreater(len(matches), 0)

    def test_resp_error_glob(self):
        matches = execute_query("method != DELETE && resp:error*", self.spec)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].path, "/items")

    def test_path_regex_operator(self):
        matches = execute_query("path ~ /users/woof.*", self.spec)
        self.assertEqual(matches, [])

        document = parse_spec_document(
            {
                "openapi": "3.0.0",
                "info": {"title": "t", "version": "1"},
                "paths": {
                    "/users/woofbar": {
                        "get": {"responses": {"200": {"description": "ok"}}}
                    }
                },
            }
        )
        matches = filter_endpoints(
            parse_query("path ~ /users/woof.*"),
            document.endpoints,
            document,
        )
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].path, "/users/woofbar")

    def test_boolean_or_in_parens(self):
        matches = execute_query("(resp:city* || resp:state)", self.spec)
        paths = {endpoint.path for endpoint in matches}
        self.assertIn("/dogs", paths)

    def test_grouped_or_and_path(self):
        matches = execute_query("(POST || PUT) && /users* && reqpath:id", self.spec)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].method, "PUT")
        self.assertEqual(matches[0].path, "/users/{id}")

    def test_req_email_and_password(self):
        matches = execute_query("req:email && req:password", self.spec)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].method, "POST")
        self.assertEqual(matches[0].path, "/users")

    def test_prd_complete_example(self):
        query = "GET /dogs* req:dogna.* resp:owner* (resp:city* || resp:state)"
        matches = execute_query(query, self.spec)
        self.assertEqual(len(matches), 0)

        matches = execute_query(
            "GET /dogs* req:dogName.* resp:owner* (resp:city* || resp:state)",
            self.spec,
        )
        self.assertEqual(len(matches), 2)
        paths = {endpoint.path for endpoint in matches}
        self.assertEqual(paths, {"/dogs", "/dogs/{id}"})

    def test_path_template_exact_and_glob(self):
        matches = execute_query("/user/{name}", self.spec)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].path, "/user/{name}")

        glob_matches = execute_query("/user/*", self.spec)
        self.assertEqual(len(glob_matches), 1)

        no_match = execute_query("/user/{id}", self.spec)
        self.assertEqual(no_match, [])


class TestFreeTextSearch(unittest.TestCase):
    def test_logout_ranks_path_before_parameter_match(self):
        document = parse_spec_document(
            {
                "openapi": "3.0.0",
                "info": {"title": "t", "version": "1"},
                "paths": {
                    "/user/logout": {
                        "get": {
                            "operationId": "logoutUser",
                            "summary": "Logs out current logged in user session",
                            "responses": {"200": {"description": "ok"}},
                        }
                    },
                    "/session": {
                        "post": {
                            "operationId": "createSession",
                            "summary": "Create a session",
                            "parameters": [
                                {
                                    "name": "logoutReason",
                                    "in": "query",
                                    "schema": {"type": "string"},
                                }
                            ],
                            "responses": {"200": {"description": "ok"}},
                        }
                    },
                    "/account/{id}": {
                        "delete": {
                            "operationId": "deleteAccount",
                            "summary": "Delete account",
                            "parameters": [
                                {
                                    "name": "id",
                                    "in": "path",
                                    "required": True,
                                    "schema": {"type": "integer"},
                                }
                            ],
                            "responses": {"200": {"description": "ok"}},
                        }
                    },
                },
            }
        )

        matches = execute_query("logout", document)
        self.assertGreaterEqual(len(matches), 2)
        self.assertEqual(matches[0].path, "/user/logout")
        self.assertEqual(matches[0].method, "GET")

    def test_free_text_matches_operation_id_and_summary(self):
        document = parse_spec_document(
            {
                "openapi": "3.0.0",
                "info": {"title": "t", "version": "1"},
                "paths": {
                    "/events": {
                        "get": {
                            "operationId": "listAuditEvents",
                            "summary": "List audit events",
                            "responses": {"200": {"description": "ok"}},
                        }
                    },
                    "/health": {
                        "get": {
                            "operationId": "healthCheck",
                            "summary": "Health check",
                            "responses": {"200": {"description": "ok"}},
                        }
                    },
                },
            }
        )

        matches = execute_query("audit", document)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].path, "/events")

    def test_endpoint_search_index_includes_metadata(self):
        document = parse_spec_document(
            {
                "openapi": "3.0.0",
                "info": {"title": "t", "version": "1"},
                "components": {
                    "schemas": {
                        "User": {
                            "type": "object",
                            "properties": {
                                "username": {"type": "string"},
                            },
                        }
                    }
                },
                "paths": {
                    "/user": {
                        "post": {
                            "operationId": "createUser",
                            "summary": "Create user",
                            "requestBody": {
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "$ref": "#/components/schemas/User"
                                        }
                                    }
                                }
                            },
                            "responses": {"200": {"description": "ok"}},
                        }
                    }
                },
            }
        )
        from restx.core.matcher import endpoint_search_index

        endpoint = document.endpoints[0]
        index = endpoint_search_index(endpoint, document)
        self.assertEqual(index["path"], ("/user",))
        self.assertIn("createUser", index["operation"])
        self.assertIn("Create user", index["operation"])
        self.assertIn("body", index["parameters"])
        self.assertIn("username", index["parameters"])


if __name__ == "__main__":
    unittest.main()
