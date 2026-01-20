from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from uuid import UUID

from tests._bootstrap import bootstrap_backend_imports, reset_caches
from tests._db import make_session


bootstrap_backend_imports()
reset_caches()

from app.common.exceptions import ApiException  # noqa: E402


class EntryServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = make_session()

        from app.entry_type.models import EntryType  # noqa: E402
        from app.tag.models import Tag  # noqa: E402

        self.et = EntryType(code="t", name="T", graph_enabled=True, ai_enabled=True, enabled=True)
        self.db.add(self.et)
        self.db.commit()

        self.tag1 = Tag(name="tag1", color=None, description=None)
        self.tag2 = Tag(name="tag2", color=None, description=None)
        self.db.add_all([self.tag1, self.tag2])
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()

    def test_create_invalid_tag_ids_raises_40001(self) -> None:
        from app.entry.schemas import EntryRequest  # noqa: E402
        from app.entry.models import TimeMode  # noqa: E402
        from app.entry.service import EntryService  # noqa: E402

        svc = EntryService(self.db)
        with self.assertRaises(ApiException) as ctx:
            svc.create(
                EntryRequest(
                    title="t",
                    summary=None,
                    content="c",
                    type_id=self.et.id,
                    time_mode=TimeMode.POINT,
                    time_at=datetime.now(timezone.utc),
                    tag_ids=[self.tag1.id, UUID("00000000-0000-0000-0000-000000000001")],
                )
            )
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(ctx.exception.code, 40001)

    def test_find_by_id_404(self) -> None:
        from app.entry.service import EntryService  # noqa: E402

        svc = EntryService(self.db)
        with self.assertRaises(ApiException) as ctx:
            svc.find_by_id(UUID("00000000-0000-0000-0000-000000000001"))
        self.assertEqual(ctx.exception.status_code, 404)

    def test_update_invalid_tag_ids_raises_40001(self) -> None:
        from app.entry.models import Entry, TimeMode  # noqa: E402
        from app.entry.schemas import EntryRequest  # noqa: E402
        from app.entry.service import EntryService  # noqa: E402

        entry = Entry(
            title="t",
            content="c",
            type_id=self.et.id,
            time_mode=TimeMode.POINT,
            time_at=datetime.now(timezone.utc),
        )
        self.db.add(entry)
        self.db.commit()

        svc = EntryService(self.db)
        with self.assertRaises(ApiException) as ctx:
            svc.update(
                entry.id,
                EntryRequest(
                    title="t2",
                    summary=None,
                    content="c2",
                    type_id=self.et.id,
                    time_mode=TimeMode.POINT,
                    time_at=datetime.now(timezone.utc),
                    tag_ids=[UUID("00000000-0000-0000-0000-000000000001")],
                ),
            )
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(ctx.exception.code, 40001)

    def test_search_time_intersection_and_pagination(self) -> None:
        from app.entry.models import Entry, TimeMode  # noqa: E402
        from app.entry.schemas import EntrySearchRequest  # noqa: E402
        from app.entry.service import EntryService  # noqa: E402

        e_point_in = Entry(
            title="point-in",
            content="hello",
            type_id=self.et.id,
            time_mode=TimeMode.POINT,
            time_at=datetime(2026, 1, 4, tzinfo=timezone.utc),
        )
        e_point_out = Entry(
            title="point-out",
            content="hello",
            type_id=self.et.id,
            time_mode=TimeMode.POINT,
            time_at=datetime(2026, 1, 20, tzinfo=timezone.utc),
        )
        e_range = Entry(
            title="range",
            content="hello",
            type_id=self.et.id,
            time_mode=TimeMode.RANGE,
            time_from=datetime(2026, 1, 5, tzinfo=timezone.utc),
            time_to=datetime(2026, 1, 10, tzinfo=timezone.utc),
        )
        e_point_in.tags.append(self.tag1)
        e_range.tags.append(self.tag1)
        self.db.add_all([e_point_in, e_point_out, e_range])
        self.db.commit()

        svc = EntryService(self.db)
        res = svc.search(
            EntrySearchRequest(
                keyword="hello",
                tag_ids=[self.tag1.id],
                time_from=datetime(2026, 1, 3, tzinfo=timezone.utc),
                time_to=datetime(2026, 1, 6, tzinfo=timezone.utc),
                page=0,
                size=1,
            )
        )
        self.assertEqual(res["total"], 2)
        self.assertEqual(res["page"], 0)
        self.assertEqual(res["size"], 1)
        self.assertEqual(res["total_pages"], 2)
        self.assertEqual(len(res["content"]), 1)

    def test_create_and_update_success(self) -> None:
        from app.entry.models import TimeMode  # noqa: E402
        from app.entry.schemas import EntryRequest  # noqa: E402
        from app.entry.service import EntryService  # noqa: E402

        svc = EntryService(self.db)
        created = svc.create(
            EntryRequest(
                title="t",
                summary="s",
                content="c",
                type_id=self.et.id,
                time_mode=TimeMode.POINT,
                time_at=datetime.now(timezone.utc),
                tag_ids=[self.tag1.id],
            )
        )
        self.assertEqual(created.title, "t")
        self.assertEqual({t.id for t in created.tags}, {self.tag1.id})

        updated = svc.update(
            created.id,
            EntryRequest(
                title="t2",
                summary=None,
                content="c2",
                type_id=self.et.id,
                time_mode=TimeMode.POINT,
                time_at=datetime.now(timezone.utc),
                tag_ids=[self.tag2.id],
            ),
        )
        self.assertEqual(updated.title, "t2")
        self.assertEqual({t.id for t in updated.tags}, {self.tag2.id})

        self.assertEqual(len(svc.find_all()), 1)

    def test_delete_allows_storage_error_and_cleans_relations(self) -> None:
        from app.attachment.models import Attachment  # noqa: E402
        from app.entry.models import Entry, TimeMode  # noqa: E402
        from app.entry.service import EntryService  # noqa: E402
        from app.relation.models import Relation, RelationType  # noqa: E402

        entry = Entry(
            title="t",
            content="c",
            type_id=self.et.id,
            time_mode=TimeMode.POINT,
            time_at=datetime.now(timezone.utc),
        )
        other = Entry(
            title="o",
            content="c",
            type_id=self.et.id,
            time_mode=TimeMode.POINT,
            time_at=datetime.now(timezone.utc),
        )
        self.db.add_all([entry, other])
        self.db.commit()

        rt = RelationType(code="ref", name="Ref", directed=True, enabled=True)
        self.db.add(rt)
        self.db.commit()

        rel = Relation(
            source_entry_id=entry.id,
            target_entry_id=other.id,
            relation_type_id=rt.id,
            description=None,
        )
        self.db.add(rel)
        self.db.commit()

        att = Attachment(
            entry_id=entry.id,
            filename="f",
            original_filename="o",
            file_path="k",
            size=1,
            content_type="text/plain",
        )
        self.db.add(att)
        self.db.commit()

        svc = EntryService(self.db)

        # Simulate storage outage: should still delete DB rows.
        from app.entry import service as entry_service_module  # noqa: E402

        with patch.object(
            entry_service_module, "get_minio_client", side_effect=entry_service_module.StorageError("down")
        ):
            svc.delete(entry.id)

        self.assertIsNone(self.db.query(Entry).filter(Entry.id == entry.id).first())
        self.assertEqual(self.db.query(Attachment).filter(Attachment.entry_id == entry.id).count(), 0)
        self.assertEqual(
            self.db.query(Relation)
            .filter((Relation.source_entry_id == entry.id) | (Relation.target_entry_id == entry.id))
            .count(),
            0,
        )
