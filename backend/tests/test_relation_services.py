from __future__ import annotations

import unittest
from datetime import datetime, timezone
from uuid import UUID

from tests._bootstrap import bootstrap_backend_imports, reset_caches
from tests._db import make_session


bootstrap_backend_imports()
reset_caches()

from app.common.exceptions import ApiException  # noqa: E402


class RelationTypeServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = make_session()

    def tearDown(self) -> None:
        self.db.close()

    def test_create_duplicate_code_raises(self) -> None:
        from app.relation.schemas import RelationTypeRequest  # noqa: E402
        from app.relation.service_type import RelationTypeService  # noqa: E402

        svc = RelationTypeService(self.db)
        svc.create(
            RelationTypeRequest(
                code="ref",
                name="Ref",
                inverse_name=None,
                description=None,
                color=None,
                directed=True,
                enabled=True,
            )
        )
        with self.assertRaises(ApiException) as ctx:
            svc.create(
                RelationTypeRequest(
                    code="ref",
                    name="Dup",
                    inverse_name=None,
                    description=None,
                    color=None,
                    directed=True,
                    enabled=True,
                )
            )
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(ctx.exception.code, 40001)

    def test_find_by_id_404(self) -> None:
        from app.relation.service_type import RelationTypeService  # noqa: E402

        svc = RelationTypeService(self.db)
        with self.assertRaises(ApiException) as ctx:
            svc.find_by_id(UUID("00000000-0000-0000-0000-000000000001"))
        self.assertEqual(ctx.exception.status_code, 404)

    def test_update_duplicate_code_raises(self) -> None:
        from app.relation.schemas import RelationTypeRequest  # noqa: E402
        from app.relation.service_type import RelationTypeService  # noqa: E402

        svc = RelationTypeService(self.db)
        rt1 = svc.create(
            RelationTypeRequest(
                code="a",
                name="A",
                inverse_name=None,
                description=None,
                color=None,
                directed=True,
                enabled=True,
            )
        )
        rt2 = svc.create(
            RelationTypeRequest(
                code="b",
                name="B",
                inverse_name=None,
                description=None,
                color=None,
                directed=True,
                enabled=True,
            )
        )
        with self.assertRaises(ApiException) as ctx:
            svc.update(
                rt2.id,
                RelationTypeRequest(
                    code="a",
                    name="B",
                    inverse_name=None,
                    description=None,
                    color=None,
                    directed=True,
                    enabled=True,
                ),
            )
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(ctx.exception.code, 40001)
        updated = svc.update(
            rt1.id,
            RelationTypeRequest(
                code="a",
                name="A2",
                inverse_name=None,
                description=None,
                color=None,
                directed=True,
                enabled=True,
            ),
        )
        self.assertEqual(updated.name, "A2")


class RelationServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = make_session()

        from app.entry.models import Entry, TimeMode  # noqa: E402
        from app.entry_type.models import EntryType  # noqa: E402
        from app.relation.models import RelationType  # noqa: E402

        et = EntryType(code="t", name="T", graph_enabled=True, ai_enabled=True, enabled=True)
        self.db.add(et)
        self.db.commit()

        self.e1 = Entry(
            title="e1",
            content="c",
            type_id=et.id,
            time_mode=TimeMode.POINT,
            time_at=datetime.now(timezone.utc),
        )
        self.e2 = Entry(
            title="e2",
            content="c",
            type_id=et.id,
            time_mode=TimeMode.POINT,
            time_at=datetime.now(timezone.utc),
        )
        self.db.add_all([self.e1, self.e2])
        self.db.commit()

        self.rt = RelationType(code="ref", name="Ref", directed=True, enabled=True)
        self.db.add(self.rt)
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()

    def test_create_and_find_by_entry(self) -> None:
        from app.relation.schemas import RelationRequest  # noqa: E402
        from app.relation.service import RelationService  # noqa: E402

        svc = RelationService(self.db)
        rel = svc.create(
            RelationRequest(
                source_entry_id=self.e1.id,
                target_entry_id=self.e2.id,
                relation_type_id=self.rt.id,
                description="d",
            )
        )
        self.assertEqual(rel.source_entry.id, self.e1.id)
        self.assertEqual(rel.target_entry.id, self.e2.id)
        self.assertEqual(rel.relation_type.id, self.rt.id)

        by_e1 = svc.find_by_entry(self.e1.id)
        by_e2 = svc.find_by_entry(self.e2.id)
        self.assertEqual(len(by_e1), 1)
        self.assertEqual(len(by_e2), 1)

    def test_update_and_delete(self) -> None:
        from app.relation.models import Relation  # noqa: E402
        from app.relation.schemas import RelationRequest  # noqa: E402
        from app.relation.service import RelationService  # noqa: E402

        svc = RelationService(self.db)
        created = svc.create(
            RelationRequest(
                source_entry_id=self.e1.id,
                target_entry_id=self.e2.id,
                relation_type_id=self.rt.id,
                description="d",
            )
        )
        updated = svc.update(
            created.id,
            RelationRequest(
                source_entry_id=self.e2.id,
                target_entry_id=self.e1.id,
                relation_type_id=self.rt.id,
                description="d2",
            ),
        )
        self.assertEqual(updated.description, "d2")
        self.assertEqual(len(svc.find_all()), 1)
        svc.delete(created.id)
        self.assertEqual(self.db.query(Relation).count(), 0)

    def test_find_by_id_404(self) -> None:
        from app.relation.service import RelationService  # noqa: E402

        svc = RelationService(self.db)
        with self.assertRaises(ApiException) as ctx:
            svc.find_by_id(UUID("00000000-0000-0000-0000-000000000001"))
        self.assertEqual(ctx.exception.status_code, 404)
