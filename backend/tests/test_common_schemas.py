from __future__ import annotations

import unittest

from tests._bootstrap import bootstrap_backend_imports, reset_caches


bootstrap_backend_imports()
reset_caches()

from app.common.schemas import to_camel  # noqa: E402
from app.common.schemas import CamelModel, OrmModel  # noqa: E402


class ToCamelTests(unittest.TestCase):
    def test_to_camel_basic(self) -> None:
        self.assertEqual(to_camel("a_b_c"), "aBC")

    def test_to_camel_keeps_head(self) -> None:
        self.assertEqual(to_camel("hello_world"), "helloWorld")

    def test_to_camel_ignores_empty_segments(self) -> None:
        self.assertEqual(to_camel("a__b"), "aB")


class ModelAliasTests(unittest.TestCase):
    def test_camel_model_dumps_by_alias(self) -> None:
        class M(CamelModel):
            some_field: int

        m = M(some_field=1)
        self.assertEqual(m.model_dump(by_alias=True), {"someField": 1})

    def test_orm_model_from_attributes(self) -> None:
        class M(OrmModel):
            some_field: int

        obj = type("Obj", (), {"some_field": 2})()
        m = M.model_validate(obj)
        self.assertEqual(m.some_field, 2)
