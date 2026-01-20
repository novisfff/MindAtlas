from __future__ import annotations

import io
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

from tests._bootstrap import bootstrap_backend_imports, reset_caches
from tests._db import make_session


bootstrap_backend_imports()
reset_caches()

from app.common.exceptions import ApiException  # noqa: E402


class AttachmentServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.db = make_session()

        from app.entry.models import Entry, TimeMode  # noqa: E402
        from app.entry_type.models import EntryType  # noqa: E402

        et = EntryType(code="t", name="T", graph_enabled=True, ai_enabled=True, enabled=True)
        self.db.add(et)
        self.db.commit()

        self.entry = Entry(
            title="e",
            content=None,
            type_id=et.id,
            time_mode=TimeMode.POINT,
            time_at=datetime.now(timezone.utc),
        )
        self.db.add(self.entry)
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()

    async def test_upload_storage_unavailable_raises_50002(self) -> None:
        from app.attachment.service import AttachmentService  # noqa: E402
        from app.attachment import service as attachment_service_module  # noqa: E402

        svc = AttachmentService(self.db)
        fake_file = SimpleNamespace(filename="a.txt", content_type="text/plain", file=io.BytesIO(b"x"))

        with patch.object(
            attachment_service_module, "get_minio_client", side_effect=attachment_service_module.StorageError("down")
        ):
            with self.assertRaises(ApiException) as ctx:
                await svc.upload(self.entry.id, fake_file)
        self.assertEqual(ctx.exception.status_code, 500)
        self.assertEqual(ctx.exception.code, 50002)

    async def test_upload_put_object_error_raises_50001(self) -> None:
        from app.attachment.service import AttachmentService  # noqa: E402
        from app.attachment import service as attachment_service_module  # noqa: E402

        svc = AttachmentService(self.db)
        fake_file = SimpleNamespace(filename="a.txt", content_type="text/plain", file=io.BytesIO(b"x"))

        class FakeS3Error(Exception):
            code = "AccessDenied"

        class FakeClient:
            def put_object(self, **_kwargs):
                raise FakeS3Error()

        with (
            patch.object(attachment_service_module, "S3Error", FakeS3Error),
            patch.object(attachment_service_module, "get_minio_client", return_value=(FakeClient(), "b")),
        ):
            with self.assertRaises(ApiException) as ctx:
                await svc.upload(self.entry.id, fake_file)
        self.assertEqual(ctx.exception.status_code, 500)
        self.assertEqual(ctx.exception.code, 50001)

    async def test_upload_stat_failure_falls_back_size_zero(self) -> None:
        from app.attachment.service import AttachmentService  # noqa: E402
        from app.attachment import service as attachment_service_module  # noqa: E402
        from app.attachment.models import Attachment  # noqa: E402

        svc = AttachmentService(self.db)
        fake_file = SimpleNamespace(filename="a.txt", content_type="text/plain", file=io.BytesIO(b"x"))

        class FakeS3Error(Exception):
            code = "X"

        class FakeClient:
            def put_object(self, **_kwargs):
                return None

            def stat_object(self, _bucket: str, _object_key: str):
                raise FakeS3Error()

        with (
            patch.object(attachment_service_module, "S3Error", FakeS3Error),
            patch.object(attachment_service_module, "get_minio_client", return_value=(FakeClient(), "b")),
        ):
            att = await svc.upload(self.entry.id, fake_file)

        db_att = self.db.query(Attachment).filter(Attachment.id == att.id).first()
        self.assertIsNotNone(db_att)
        self.assertEqual(db_att.size, 0)

    async def test_upload_db_failure_cleans_object(self) -> None:
        from app.attachment.service import AttachmentService  # noqa: E402
        from app.attachment import service as attachment_service_module  # noqa: E402

        svc = AttachmentService(self.db)
        fake_file = SimpleNamespace(filename="a.txt", content_type="text/plain", file=io.BytesIO(b"x"))

        class FakeClient:
            def put_object(self, **_kwargs):
                return None

            def stat_object(self, _bucket: str, _object_key: str):
                return SimpleNamespace(size=1)

        # Force DB commit to fail during metadata save.
        with (
            patch.object(attachment_service_module, "get_minio_client", return_value=(FakeClient(), "b")),
            patch.object(attachment_service_module, "remove_object_safe", return_value=True) as rm,
            patch.object(self.db, "commit", side_effect=Exception("db down")),
        ):
            with self.assertRaises(ApiException) as ctx:
                await svc.upload(self.entry.id, fake_file)

        self.assertEqual(ctx.exception.status_code, 500)
        self.assertEqual(ctx.exception.code, 50002)
        rm.assert_called()

    def test_delete_remove_object_failed_raises_50001(self) -> None:
        from app.attachment.models import Attachment  # noqa: E402
        from app.attachment.service import AttachmentService  # noqa: E402
        from app.attachment import service as attachment_service_module  # noqa: E402

        att = Attachment(
            entry_id=self.entry.id,
            filename="f",
            original_filename="o",
            file_path="k",
            size=1,
            content_type="text/plain",
        )
        self.db.add(att)
        self.db.commit()

        svc = AttachmentService(self.db)

        with (
            patch.object(attachment_service_module, "get_minio_client", return_value=(object(), "b")),
            patch.object(attachment_service_module, "remove_object_safe", return_value=False),
        ):
            with self.assertRaises(ApiException) as ctx:
                svc.delete(att.id)
        self.assertEqual(ctx.exception.status_code, 500)
        self.assertEqual(ctx.exception.code, 50001)

    def test_delete_storage_unavailable_raises_50002(self) -> None:
        from app.attachment.models import Attachment  # noqa: E402
        from app.attachment.service import AttachmentService  # noqa: E402
        from app.attachment import service as attachment_service_module  # noqa: E402

        att = Attachment(
            entry_id=self.entry.id,
            filename="f",
            original_filename="o",
            file_path="k",
            size=1,
            content_type="text/plain",
        )
        self.db.add(att)
        self.db.commit()

        svc = AttachmentService(self.db)
        with patch.object(
            attachment_service_module, "get_minio_client", side_effect=attachment_service_module.StorageError("down")
        ):
            with self.assertRaises(ApiException) as ctx:
                svc.delete(att.id)
        self.assertEqual(ctx.exception.status_code, 500)
        self.assertEqual(ctx.exception.code, 50002)

    def test_get_object_stream_not_found_raises_404(self) -> None:
        from app.attachment.service import AttachmentService  # noqa: E402
        from app.attachment import service as attachment_service_module  # noqa: E402

        svc = AttachmentService(self.db)

        class FakeS3Error(Exception):
            def __init__(self, code: str):
                super().__init__(code)
                self.code = code

        class FakeClient:
            def stat_object(self, _bucket: str, _object_key: str):
                raise FakeS3Error("NoSuchKey")

        with (
            patch.object(attachment_service_module, "S3Error", FakeS3Error),
            patch.object(attachment_service_module, "get_minio_client", return_value=(FakeClient(), "b")),
        ):
            with self.assertRaises(ApiException) as ctx:
                svc.get_object_stream("missing")
        self.assertEqual(ctx.exception.status_code, 404)
        self.assertEqual(ctx.exception.code, 40400)

    def test_get_object_stream_other_s3_error_raises_50001(self) -> None:
        from app.attachment.service import AttachmentService  # noqa: E402
        from app.attachment import service as attachment_service_module  # noqa: E402

        svc = AttachmentService(self.db)

        class FakeS3Error(Exception):
            def __init__(self, code: str):
                super().__init__(code)
                self.code = code

        class FakeClient:
            def stat_object(self, _bucket: str, _object_key: str):
                raise FakeS3Error("AccessDenied")

        with (
            patch.object(attachment_service_module, "S3Error", FakeS3Error),
            patch.object(attachment_service_module, "get_minio_client", return_value=(FakeClient(), "b")),
        ):
            with self.assertRaises(ApiException) as ctx:
                svc.get_object_stream("x")
        self.assertEqual(ctx.exception.status_code, 500)
        self.assertEqual(ctx.exception.code, 50001)

    def test_find_all_find_by_id_find_by_entry(self) -> None:
        from app.attachment.models import Attachment  # noqa: E402
        from app.attachment.service import AttachmentService  # noqa: E402

        att = Attachment(
            entry_id=self.entry.id,
            filename="f",
            original_filename="o",
            file_path="k",
            size=1,
            content_type="text/plain",
        )
        self.db.add(att)
        self.db.commit()

        svc = AttachmentService(self.db)
        self.assertEqual([a.id for a in svc.find_all()], [att.id])
        self.assertEqual(svc.find_by_id(att.id).id, att.id)
        self.assertEqual([a.id for a in svc.find_by_entry(self.entry.id)], [att.id])

        with self.assertRaises(ApiException) as ctx:
            svc.find_by_id(UUID("00000000-0000-0000-0000-000000000001"))
        self.assertEqual(ctx.exception.status_code, 404)
