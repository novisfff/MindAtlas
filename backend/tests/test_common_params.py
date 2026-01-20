from __future__ import annotations

import unittest
from uuid import UUID

from tests._bootstrap import bootstrap_backend_imports, reset_caches


bootstrap_backend_imports()
reset_caches()

from app.common.params import parse_uuid_csv  # noqa: E402


class ParseUuidCsvTests(unittest.TestCase):
    def test_none_and_blank(self) -> None:
        self.assertEqual(parse_uuid_csv(None), [])
        self.assertEqual(parse_uuid_csv(""), [])
        self.assertEqual(parse_uuid_csv("   "), [])

    def test_parses_multiple_uuids(self) -> None:
        u1 = "00000000-0000-0000-0000-000000000001"
        u2 = "00000000-0000-0000-0000-000000000002"
        out = parse_uuid_csv(f" {u1}, {u2} ")
        self.assertEqual(out, [UUID(u1), UUID(u2)])

    def test_ignores_empty_parts(self) -> None:
        u1 = "00000000-0000-0000-0000-000000000001"
        out = parse_uuid_csv(f"{u1}, ,   ,")
        self.assertEqual(out, [UUID(u1)])

    def test_invalid_uuid_raises(self) -> None:
        with self.assertRaises(ValueError):
            parse_uuid_csv("not-a-uuid")

