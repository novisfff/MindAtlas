from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from tests._bootstrap import bootstrap_backend_imports, reset_caches
from tests._db import make_session


bootstrap_backend_imports()
reset_caches()

from app.common.exceptions import ApiException  # noqa: E402


class TagServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = make_session()

    def tearDown(self) -> None:
        self.db.close()

    def test_find_by_id_404(self) -> None:
        from app.tag.service import TagService  # noqa: E402

        svc = TagService(self.db)
        missing = UUID("00000000-0000-0000-0000-000000000001")
        with self.assertRaises(ApiException) as ctx:
            svc.find_by_id(missing)
        self.assertEqual(ctx.exception.status_code, 404)

    def test_create_case_insensitive_uniqueness(self) -> None:
        from app.tag.schemas import TagRequest  # noqa: E402
        from app.tag.service import TagService  # noqa: E402

        svc = TagService(self.db)
        svc.create(TagRequest(name="Tag", color=None, description=None))

        with self.assertRaises(ApiException) as ctx:
            svc.create(TagRequest(name="tag", color=None, description=None))
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(ctx.exception.code, 40001)

    def test_find_all_and_find_by_ids(self) -> None:
        from app.tag.schemas import TagRequest  # noqa: E402
        from app.tag.service import TagService  # noqa: E402

        svc = TagService(self.db)
        t1 = svc.create(TagRequest(name="a", color=None, description=None))
        t2 = svc.create(TagRequest(name="b", color=None, description=None))
        self.assertEqual({t.id for t in svc.find_all()}, {t1.id, t2.id})
        self.assertEqual({t.id for t in svc.find_by_ids([t1.id])}, {t1.id})

    def test_update_allows_case_change_but_blocks_duplicates(self) -> None:
        from app.tag.schemas import TagRequest  # noqa: E402
        from app.tag.service import TagService  # noqa: E402

        svc = TagService(self.db)
        t1 = svc.create(TagRequest(name="Tag", color=None, description=None))
        t2 = svc.create(TagRequest(name="Other", color=None, description=None))

        updated = svc.update(t1.id, TagRequest(name="TAG", color=None, description=None))
        self.assertEqual(updated.name, "TAG")

        with self.assertRaises(ApiException) as ctx:
            svc.update(t2.id, TagRequest(name="tag", color=None, description=None))
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(ctx.exception.code, 40001)

    def test_delete_clears_entry_tag_association(self) -> None:
        from sqlalchemy import select  # noqa: E402

        from app.entry.models import Entry, TimeMode, entry_tag  # noqa: E402
        from app.entry_type.models import EntryType  # noqa: E402
        from app.tag.models import Tag  # noqa: E402
        from app.tag.schemas import TagRequest  # noqa: E402
        from app.tag.service import TagService  # noqa: E402

        et = EntryType(code="t", name="T", graph_enabled=True, ai_enabled=True, enabled=True)
        self.db.add(et)
        self.db.commit()

        entry = Entry(
            title="e",
            content="c",
            type_id=et.id,
            time_mode=TimeMode.POINT,
            time_at=datetime.now(timezone.utc),
        )
        self.db.add(entry)
        self.db.commit()

        svc = TagService(self.db)
        tag = svc.create(TagRequest(name="X", color=None, description=None))

        entry.tags.append(tag)
        self.db.commit()

        before = self.db.execute(select(entry_tag)).all()
        self.assertEqual(len(before), 1)

        svc.delete(tag.id)

        after = self.db.execute(select(entry_tag)).all()
        self.assertEqual(after, [])
        self.assertIsNone(self.db.query(Tag).filter(Tag.id == tag.id).first())

    def test_delete_integrity_error_raises_409(self) -> None:
        from app.tag.service import TagService  # noqa: E402

        db = MagicMock()
        svc = TagService(db)
        svc.find_by_id = MagicMock(return_value=object())
        db.commit.side_effect = IntegrityError("stmt", "params", Exception("orig"))

        with self.assertRaises(ApiException) as ctx:
            svc.delete(UUID("00000000-0000-0000-0000-000000000001"))
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.code, 40900)
        db.rollback.assert_called()
