from __future__ import annotations

import unittest

from tests._bootstrap import bootstrap_backend_imports, reset_caches


bootstrap_backend_imports()
reset_caches()

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.common.exceptions import ApiException, register_exception_handlers  # noqa: E402


class ExceptionHandlersTests(unittest.TestCase):
    def _make_app(self) -> FastAPI:
        app = FastAPI()
        register_exception_handlers(app)

        @app.get("/api_exc")
        def api_exc():
            raise ApiException(status_code=400, code=40001, message="X", details={"d": 1})

        @app.get("/http_exc")
        def http_exc():
            raise HTTPException(status_code=403, detail="Forbidden")

        @app.get("/boom")
        def boom():
            raise RuntimeError("boom")

        @app.get("/validate")
        def validate(q: int):  # noqa: B008
            return {"q": q}

        return app

    def test_api_exception_handler(self) -> None:
        client = TestClient(self._make_app())
        resp = client.get("/api_exc")
        self.assertEqual(resp.status_code, 400)
        payload = resp.json()
        self.assertEqual(payload["success"], False)
        self.assertEqual(payload["code"], 40001)
        self.assertEqual(payload["message"], "X")
        self.assertEqual(payload["data"], {"d": 1})

    def test_starlette_http_exception_handler(self) -> None:
        client = TestClient(self._make_app())
        resp = client.get("/http_exc")
        self.assertEqual(resp.status_code, 403)
        payload = resp.json()
        self.assertEqual(payload["success"], False)
        self.assertEqual(payload["code"], 403)
        self.assertEqual(payload["message"], "Forbidden")

    def test_unhandled_exception_handler(self) -> None:
        client = TestClient(self._make_app(), raise_server_exceptions=False)
        resp = client.get("/boom")
        self.assertEqual(resp.status_code, 500)
        payload = resp.json()
        self.assertEqual(payload["success"], False)
        self.assertEqual(payload["code"], 50000)
        self.assertEqual(payload["message"], "Internal Server Error")

    def test_request_validation_error_handler(self) -> None:
        client = TestClient(self._make_app())
        resp = client.get("/validate?q=not-int")
        self.assertEqual(resp.status_code, 422)
        payload = resp.json()
        self.assertEqual(payload["success"], False)
        self.assertEqual(payload["code"], 42200)
        self.assertEqual(payload["message"], "Validation Error")
        self.assertIsInstance(payload["data"], list)
