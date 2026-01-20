from __future__ import annotations

import unittest
from uuid import UUID

from tests._bootstrap import bootstrap_backend_imports, reset_caches
from tests._db import make_session


bootstrap_backend_imports()
reset_caches()

from app.common.exceptions import ApiException  # noqa: E402


class EntryTypeServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = make_session()

    def tearDown(self) -> None:
        self.db.close()

    def test_find_by_id_404(self) -> None:
        from app.entry_type.service import EntryTypeService  # noqa: E402

        svc = EntryTypeService(self.db)
        missing = UUID("00000000-0000-0000-0000-000000000001")
        with self.assertRaises(ApiException) as ctx:
            svc.find_by_id(missing)
        self.assertEqual(ctx.exception.status_code, 404)

    def test_find_by_code_404_and_find_all(self) -> None:
        from app.entry_type.schemas import EntryTypeRequest  # noqa: E402
        from app.entry_type.service import EntryTypeService  # noqa: E402

        svc = EntryTypeService(self.db)
        with self.assertRaises(ApiException) as ctx:
            svc.find_by_code("missing")
        self.assertEqual(ctx.exception.status_code, 404)

        created = svc.create(
            EntryTypeRequest(
                code="knowledge",
                name="Knowledge",
                description=None,
                color=None,
                icon=None,
                graph_enabled=True,
                ai_enabled=True,
                enabled=True,
            )
        )
        found = svc.find_by_code("knowledge")
        self.assertEqual(found.id, created.id)
        self.assertEqual(len(svc.find_all()), 1)

    def test_create_and_update_code_uniqueness(self) -> None:
        from app.entry_type.schemas import EntryTypeRequest  # noqa: E402
        from app.entry_type.service import EntryTypeService  # noqa: E402

        svc = EntryTypeService(self.db)
        t1 = svc.create(
            EntryTypeRequest(
                code="knowledge",
                name="Knowledge",
                description=None,
                color=None,
                icon=None,
                graph_enabled=True,
                ai_enabled=True,
                enabled=True,
            )
        )

        with self.assertRaises(ApiException) as ctx:
            svc.create(
                EntryTypeRequest(
                    code="knowledge",
                    name="Dup",
                    description=None,
                    color=None,
                    icon=None,
                    graph_enabled=True,
                    ai_enabled=True,
                    enabled=True,
                )
            )
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(ctx.exception.code, 40001)

        t2 = svc.create(
            EntryTypeRequest(
                code="project",
                name="Project",
                description=None,
                color=None,
                icon=None,
                graph_enabled=True,
                ai_enabled=True,
                enabled=True,
            )
        )

        with self.assertRaises(ApiException) as ctx2:
            svc.update(
                t2.id,
                EntryTypeRequest(
                    code="knowledge",
                    name="Project",
                    description=None,
                    color=None,
                    icon=None,
                    graph_enabled=True,
                    ai_enabled=True,
                    enabled=True,
                ),
            )
        self.assertEqual(ctx2.exception.status_code, 400)
        self.assertEqual(ctx2.exception.code, 40001)

        updated = svc.update(
            t1.id,
            EntryTypeRequest(
                code="knowledge",
                name="Knowledge2",
                description=None,
                color=None,
                icon=None,
                graph_enabled=True,
                ai_enabled=True,
                enabled=True,
            ),
        )
        self.assertEqual(updated.name, "Knowledge2")

    def test_delete_referenced_by_entry_raises_409(self) -> None:
        from datetime import datetime, timezone

        from app.entry.models import Entry, TimeMode  # noqa: E402
        from app.entry_type.schemas import EntryTypeRequest  # noqa: E402
        from app.entry_type.service import EntryTypeService  # noqa: E402

        svc = EntryTypeService(self.db)
        et = svc.create(
            EntryTypeRequest(
                code="knowledge",
                name="Knowledge",
                description=None,
                color=None,
                icon=None,
                graph_enabled=True,
                ai_enabled=True,
                enabled=True,
            )
        )

        e = Entry(
            title="t",
            content="c",
            type_id=et.id,
            time_mode=TimeMode.POINT,
            time_at=datetime.now(timezone.utc),
        )
        self.db.add(e)
        self.db.commit()

        with self.assertRaises(ApiException) as ctx:
            svc.delete(et.id)
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.code, 40900)
