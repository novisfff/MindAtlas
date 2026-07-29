from __future__ import annotations

import unittest

from tests._bootstrap import bootstrap_backend_imports, reset_caches


bootstrap_backend_imports()
reset_caches()

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from pydantic import BaseModel, model_validator  # noqa: E402

from app.common.exceptions import ApiException, register_exception_handlers  # noqa: E402


class _ValidatedBody(BaseModel):
    value: str

    @model_validator(mode="after")
    def _validate_value(self) -> "_ValidatedBody":
        raise ValueError("value is invalid")


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

        @app.post("/validate_model")
        def validate_model(body: _ValidatedBody):  # noqa: B008, ANN001
            return {"value": body.value}

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

    def test_request_validation_error_handler_serializes_value_error_ctx(self) -> None:
        client = TestClient(self._make_app())
        resp = client.post("/validate_model", json={"value": "x"})
        self.assertEqual(resp.status_code, 422)
        payload = resp.json()
        self.assertEqual(payload["success"], False)
        self.assertEqual(payload["code"], 42200)
        self.assertIsInstance(payload["data"], list)
        self.assertIn("value is invalid", str(payload["data"]))

    def test_request_validation_error_handler_strips_input(self) -> None:
        """Pydantic v2 includes submitted values under ``input`` — never echo them."""
        from pydantic import Field

        class _SecretBody(BaseModel):
            password: str = Field(min_length=12)
            token: str = Field(min_length=8)

        app = FastAPI()
        register_exception_handlers(app)

        @app.post("/change-secret")
        def change_secret(body: _SecretBody):  # noqa: B008, ANN001
            return {"ok": True}

        client = TestClient(app)
        secret_password = "short-pw!!"
        secret_token = "tok!!"
        resp = client.post(
            "/change-secret",
            json={"password": secret_password, "token": secret_token},
        )
        self.assertEqual(resp.status_code, 422)
        dumped = resp.text
        self.assertNotIn(secret_password, dumped)
        self.assertNotIn(secret_token, dumped)
        payload = resp.json()
        self.assertEqual(payload["code"], 42200)
        self.assertIsInstance(payload["data"], list)
        for error in payload["data"]:
            self.assertIsInstance(error, dict)
            self.assertNotIn("input", error)
            self.assertIn("loc", error)
            self.assertIn("msg", error)
            self.assertIn("type", error)

    def test_sanitize_validation_errors_helper_redacts_input(self) -> None:
        from app.common.exceptions import sanitize_validation_errors

        raw = [
            {
                "type": "string_too_short",
                "loc": ("body", "newPassword"),
                "msg": "String should have at least 12 characters",
                "input": "leaked-secret-value",
                "ctx": {"min_length": 12},
            }
        ]
        cleaned = sanitize_validation_errors(raw)
        self.assertEqual(len(cleaned), 1)
        self.assertNotIn("input", cleaned[0])
        self.assertNotIn("leaked-secret-value", str(cleaned))
        self.assertEqual(cleaned[0]["loc"], ["body", "newPassword"])
        self.assertEqual(cleaned[0]["ctx"], {"min_length": 12})
