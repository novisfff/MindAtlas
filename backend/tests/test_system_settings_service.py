from __future__ import annotations

import unittest

from tests._bootstrap import bootstrap_backend_imports, reset_caches
from tests._db import make_session


bootstrap_backend_imports()
reset_caches()


class SystemSettingsServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_caches()
        self.db = make_session()

    def tearDown(self) -> None:
        self.db.close()

    def test_resolve_system_locale_uses_preferred_then_request_then_persisted(self) -> None:
        from app.common.request_context import reset_request_locale, set_request_locale
        from app.system_settings.service import SystemSettingsService, resolve_system_locale

        service = SystemSettingsService(self.db)

        self.assertEqual(resolve_system_locale(self.db), "zh")
        self.assertEqual(service.resolve_locale_response(preferred_locale="en"), ("en", False))

        service.set_locale("en")
        self.assertEqual(resolve_system_locale(self.db), "en")
        self.assertEqual(service.resolve_locale_response(), ("en", True))

        token = set_request_locale("zh")
        try:
            self.assertEqual(resolve_system_locale(self.db), "zh")
            self.assertEqual(resolve_system_locale(self.db, preferred_locale="en"), "en")
        finally:
            reset_request_locale(token)

    def test_set_locale_rejects_invalid_value(self) -> None:
        from app.system_settings.service import SystemSettingsService

        service = SystemSettingsService(self.db)
        with self.assertRaises(ValueError):
            service.set_locale("fr")


if __name__ == "__main__":
    unittest.main()
