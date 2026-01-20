from __future__ import annotations

import unittest

from tests._bootstrap import bootstrap_backend_imports, reset_caches


bootstrap_backend_imports()
reset_caches()

from app.common.responses import ApiResponse  # noqa: E402


class ApiResponseTests(unittest.TestCase):
    def test_ok_defaults(self) -> None:
        r = ApiResponse.ok({"a": 1})
        self.assertTrue(r.success)
        self.assertEqual(r.code, 0)
        self.assertEqual(r.message, "OK")
        self.assertEqual(r.data, {"a": 1})

    def test_fail(self) -> None:
        r = ApiResponse.fail(code=40001, message="Bad", data={"x": 2})
        self.assertFalse(r.success)
        self.assertEqual(r.code, 40001)
        self.assertEqual(r.message, "Bad")
        self.assertEqual(r.data, {"x": 2})

