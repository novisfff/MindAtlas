from __future__ import annotations

import unittest

from tests._bootstrap import bootstrap_backend_imports, reset_caches
from tests._db import make_session


bootstrap_backend_imports()
reset_caches()

from app.common.exceptions import ApiException  # noqa: E402


class AiProviderServiceDbTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = make_session()

    def tearDown(self) -> None:
        self.db.close()

    def test_find_all_find_by_id_find_active_and_delete(self) -> None:
        from app.ai_provider.models import AiProvider  # noqa: E402
        from app.ai_provider.service import AiProviderService  # noqa: E402

        p1 = AiProvider(
            name="p1",
            base_url="https://x",
            model="m",
            api_key_encrypted="enc",
            api_key_hint="****",
            is_active=False,
        )
        self.db.add(p1)
        self.db.commit()

        svc = AiProviderService(self.db)
        self.assertEqual([p.id for p in svc.find_all()], [p1.id])
        self.assertEqual(svc.find_by_id(p1.id).id, p1.id)
        self.assertIsNone(svc.find_active())

        svc.activate(p1.id)
        self.assertIsNotNone(svc.find_active())

        svc.delete(p1.id)
        self.assertEqual(svc.find_all(), [])

        with self.assertRaises(ApiException) as ctx:
            svc.find_by_id(p1.id)
        self.assertEqual(ctx.exception.status_code, 404)

