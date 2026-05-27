"""Unit tests for context-aware tab completion."""

import os
import unittest

from prompt_toolkit.document import Document

from restx.cli.completer import (
    CompletionKind,
    RestXCompleter,
    detect_completion_context,
)
from restx.core import QueryContext, load_spec_from_file

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


def _load_dsl_fixture():
    path = os.path.join(FIXTURES_DIR, "sample_dsl.json")
    return load_spec_from_file(path)


def _completion_texts(document_text: str, spec, context=None) -> list[str]:
    context = context or QueryContext()
    completer = RestXCompleter(spec, context)
    document = Document(document_text, len(document_text))
    return sorted(
        completion.text
        for completion in completer.get_completions(document, None)
    )


class TestCompletionContextDetection(unittest.TestCase):
    def test_start_of_line(self):
        ctx = detect_completion_context("")
        self.assertEqual(ctx.kind, CompletionKind.START)
        self.assertEqual(ctx.partial, "")

    def test_after_operator_restarts(self):
        ctx = detect_completion_context("GET /users* && ")
        self.assertEqual(ctx.kind, CompletionKind.START)

    def test_after_or_operator(self):
        ctx = detect_completion_context("(resp:city* || ")
        self.assertEqual(ctx.kind, CompletionKind.START)

    def test_after_method_offers_paths(self):
        ctx = detect_completion_context("GET ")
        self.assertEqual(ctx.kind, CompletionKind.PATH)
        self.assertEqual(ctx.method_filter, "GET")

    def test_path_partial(self):
        ctx = detect_completion_context("GET /us")
        self.assertEqual(ctx.kind, CompletionKind.PATH)
        self.assertEqual(ctx.partial, "/us")

    def test_req_prefix(self):
        ctx = detect_completion_context("req:em")
        self.assertEqual(ctx.kind, CompletionKind.REQ)
        self.assertEqual(ctx.partial, "em")

    def test_resp_prefix(self):
        ctx = detect_completion_context("resp:own")
        self.assertEqual(ctx.kind, CompletionKind.RESP)
        self.assertEqual(ctx.partial, "own")

    def test_reqpath_prefix(self):
        ctx = detect_completion_context("reqpath:i")
        self.assertEqual(ctx.kind, CompletionKind.REQPATH)
        self.assertEqual(ctx.partial, "i")


class TestRestXCompleter(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = _load_dsl_fixture()

    def test_start_completions_include_methods_prefixes_and_commands(self):
        completions = _completion_texts("", self.spec)
        self.assertIn("GET", completions)
        self.assertIn("POST", completions)
        self.assertIn("req:", completions)
        self.assertIn("resp:", completions)
        self.assertIn("reqpath:", completions)
        self.assertIn(".help", completions)
        self.assertIn(".context reset", completions)
        self.assertIn(".quit", completions)
        self.assertNotIn(".reset", completions)

    def test_dot_prefix_completions(self):
        completions = _completion_texts(".", self.spec)
        self.assertIn(".help", completions)
        self.assertIn(".ls", completions)
        self.assertIn(".shell", completions)
        self.assertIn(".curl", completions)

    def test_partial_dot_command_completions(self):
        completions = _completion_texts(".he", self.spec)
        self.assertEqual(completions, [".help"])

    def test_partial_context_reset_completes_to_dot_form(self):
        completions = _completion_texts(".context re", self.spec)
        self.assertIn(".context reset", completions)

    def test_path_completions_filtered_by_method(self):
        completions = _completion_texts("GET ", self.spec)
        self.assertIn("/users", completions)
        self.assertIn("/users/{id}", completions)
        self.assertNotIn("/users/bulk", completions)

    def test_post_path_completions(self):
        completions = _completion_texts("POST ", self.spec)
        self.assertIn("/users/bulk", completions)
        self.assertNotIn("/users/{id}", completions)

    def test_req_completions_from_spec(self):
        completions = _completion_texts("req:", self.spec)
        self.assertIn("email", completions)
        self.assertIn("password", completions)
        self.assertIn("dogName", completions)

    def test_resp_completions_from_spec(self):
        completions = _completion_texts("resp:", self.spec)
        self.assertIn("ownerName", completions)
        self.assertIn("city", completions)
        self.assertIn("state", completions)

    def test_reqpath_completions(self):
        completions = _completion_texts("reqpath:", self.spec)
        self.assertIn("id", completions)
        self.assertIn("name", completions)

    def test_filtered_req_completions_after_method_and_path(self):
        completions = _completion_texts("GET /users* req:", self.spec)
        self.assertIn("email", completions)
        self.assertIn("name", completions)
        self.assertNotIn("password", completions)
        self.assertNotIn("dogName", completions)

    def test_prefix_partial_completion(self):
        completions = _completion_texts("re", self.spec)
        self.assertIn("req:", completions)
        self.assertNotIn(".reset", completions)

    def test_inside_parentheses(self):
        completions = _completion_texts("(G", self.spec)
        self.assertIn("GET", completions)


if __name__ == "__main__":
    unittest.main()
