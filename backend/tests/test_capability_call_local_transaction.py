"""Plan 08 local transactional write tests.

Task 1: storage-only placeholder so the focused gate module path exists.
Real UoW / create_in_uow / kill-matrix coverage lands in Task 6.
"""

from __future__ import annotations

import unittest

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()


class LocalTransactionStoragePlaceholderTests(unittest.TestCase):
    def test_local_transactional_mode_is_declared(self) -> None:
        from app.assistant.capability_calls.contracts import CAPABILITY_EXECUTION_MODES

        self.assertIn("local_transactional", CAPABILITY_EXECUTION_MODES)

    def test_entry_model_exposes_source_capability_call_id(self) -> None:
        from app.entry.models import Entry

        self.assertTrue(hasattr(Entry, "source_capability_call_id"))


if __name__ == "__main__":
    unittest.main()
