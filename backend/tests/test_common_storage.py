from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from tests._bootstrap import bootstrap_backend_imports, reset_caches


bootstrap_backend_imports()


class StorageTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_caches()

    def test_get_minio_client_missing_credentials(self) -> None:
        os.environ["MINIO_ENDPOINT"] = "localhost:9000"
        os.environ["MINIO_ACCESS_KEY"] = ""
        os.environ["MINIO_SECRET_KEY"] = ""
        os.environ["MINIO_BUCKET"] = "mindatlas"
        reset_caches()

        from app.common.storage import StorageError, get_minio_client  # noqa: E402

        with self.assertRaises(StorageError):
            get_minio_client()

    def test_get_minio_client_missing_endpoint(self) -> None:
        os.environ["MINIO_ENDPOINT"] = ""
        os.environ["MINIO_ACCESS_KEY"] = "ak"
        os.environ["MINIO_SECRET_KEY"] = "sk"
        os.environ["MINIO_BUCKET"] = "b"
        reset_caches()

        from app.common.storage import StorageError, get_minio_client  # noqa: E402

        with self.assertRaises(StorageError):
            get_minio_client()

    def test_get_minio_client_parses_scheme_and_secure(self) -> None:
        os.environ["MINIO_ENDPOINT"] = "https://example.com:9000"
        os.environ["MINIO_ACCESS_KEY"] = "ak"
        os.environ["MINIO_SECRET_KEY"] = "sk"
        os.environ["MINIO_BUCKET"] = "b"
        os.environ["MINIO_SECURE"] = "false"
        reset_caches()

        captured: dict[str, object] = {}

        class FakeMinio:
            def __init__(self, endpoint: str, access_key: str, secret_key: str, secure: bool):
                captured["endpoint"] = endpoint
                captured["access_key"] = access_key
                captured["secret_key"] = secret_key
                captured["secure"] = secure

            def bucket_exists(self, bucket: str) -> bool:  # noqa: ARG002
                return True

        with patch("app.common.storage.Minio", FakeMinio):
            from app.common.storage import get_minio_client  # noqa: E402

            client, bucket = get_minio_client()

        self.assertEqual(bucket, "b")
        self.assertIsNotNone(client)
        self.assertEqual(captured["endpoint"], "example.com:9000")
        self.assertEqual(captured["secure"], True)

    def test_get_minio_client_creates_bucket(self) -> None:
        os.environ["MINIO_ENDPOINT"] = "localhost:9000"
        os.environ["MINIO_ACCESS_KEY"] = "ak"
        os.environ["MINIO_SECRET_KEY"] = "sk"
        os.environ["MINIO_BUCKET"] = "b"
        reset_caches()

        calls: list[str] = []

        class FakeMinio:
            def __init__(self, *_args, **_kwargs):
                pass

            def bucket_exists(self, bucket: str) -> bool:
                calls.append(f"exists:{bucket}")
                return False

            def make_bucket(self, bucket: str) -> None:
                calls.append(f"make:{bucket}")

        with patch("app.common.storage.Minio", FakeMinio):
            from app.common.storage import get_minio_client  # noqa: E402

            get_minio_client()

        self.assertEqual(calls, ["exists:b", "make:b"])

    def test_get_minio_client_bucket_init_failure(self) -> None:
        os.environ["MINIO_ENDPOINT"] = "localhost:9000"
        os.environ["MINIO_ACCESS_KEY"] = "ak"
        os.environ["MINIO_SECRET_KEY"] = "sk"
        os.environ["MINIO_BUCKET"] = "b"
        reset_caches()

        class FakeS3Error(Exception):
            def __init__(self, code: str):
                super().__init__(code)
                self.code = code

        class FakeMinio:
            def __init__(self, *_args, **_kwargs):
                pass

            def bucket_exists(self, bucket: str) -> bool:  # noqa: ARG002
                return False

            def make_bucket(self, _bucket: str) -> None:
                raise FakeS3Error("AccessDenied")

        with (
            patch("app.common.storage.S3Error", FakeS3Error),
            patch("app.common.storage.Minio", FakeMinio),
        ):
            from app.common.storage import StorageError, get_minio_client  # noqa: E402

            with self.assertRaises(StorageError):
                get_minio_client()

    def test_remove_object_safe_returns_true_on_not_found(self) -> None:
        reset_caches()
        from app.common.storage import remove_object_safe  # noqa: E402

        class FakeS3Error(Exception):
            def __init__(self, code: str):
                super().__init__(code)
                self.code = code

        class FakeClient:
            def remove_object(self, _bucket: str, _object_key: str) -> None:
                raise FakeS3Error("NoSuchKey")

        with patch("app.common.storage.S3Error", FakeS3Error):
            ok = remove_object_safe(FakeClient(), "b", "k")
        self.assertTrue(ok)

    def test_remove_object_safe_returns_false_on_other_errors(self) -> None:
        reset_caches()
        from app.common.storage import remove_object_safe  # noqa: E402

        class FakeS3Error(Exception):
            def __init__(self, code: str):
                super().__init__(code)
                self.code = code

        class FakeClient:
            def remove_object(self, _bucket: str, _object_key: str) -> None:
                raise FakeS3Error("AccessDenied")

        with patch("app.common.storage.S3Error", FakeS3Error):
            ok = remove_object_safe(FakeClient(), "b", "k")
        self.assertFalse(ok)
