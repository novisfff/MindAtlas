from __future__ import annotations

from uuid import UUID
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.ai_provider.crypto import api_key_hint, decrypt_api_key, encrypt_api_key
from app.ai_provider.models import AiProvider
from app.ai_provider.schemas import (
    AiProviderCreateRequest,
    AiProviderTestConnectionResponse,
    AiProviderUpdateRequest,
    FetchModelsRequest,
    FetchModelsResponse,
)
from app.common.exceptions import ApiException


class AiProviderService:
    def __init__(self, db: Session):
        self.db = db

    def find_all(self) -> list[AiProvider]:
        return self.db.query(AiProvider).order_by(AiProvider.created_at.desc()).all()

    def find_by_id(self, id: UUID) -> AiProvider:
        provider = self.db.query(AiProvider).filter(AiProvider.id == id).first()
        if not provider:
            raise ApiException(status_code=404, code=40400, message=f"AiProvider not found: {id}")
        return provider

    def find_active(self) -> AiProvider | None:
        return self.db.query(AiProvider).filter(AiProvider.is_active.is_(True)).first()

    def create(self, request: AiProviderCreateRequest) -> AiProvider:
        existing = self.db.query(AiProvider).filter(AiProvider.name.ilike(request.name)).first()
        if existing:
            raise ApiException(
                status_code=400, code=40001, message=f"AiProvider name already exists: {request.name}"
            )

        try:
            encrypted = encrypt_api_key(request.api_key)
        except Exception as exc:
            raise ApiException(
                status_code=500, code=50001, message="AI_PROVIDER_FERNET_KEY not configured"
            ) from exc

        provider = AiProvider(
            name=request.name,
            base_url=request.base_url,
            model=request.model,
            api_key_encrypted=encrypted,
            api_key_hint=api_key_hint(request.api_key),
            is_active=False,
        )
        self.db.add(provider)
        self.db.commit()
        self.db.refresh(provider)
        return provider

    def update(self, id: UUID, request: AiProviderUpdateRequest) -> AiProvider:
        provider = self.find_by_id(id)

        if request.name is not None and provider.name.lower() != request.name.lower():
            existing = self.db.query(AiProvider).filter(AiProvider.name.ilike(request.name)).first()
            if existing:
                raise ApiException(
                    status_code=400, code=40001, message=f"AiProvider name already exists: {request.name}"
                )
            provider.name = request.name

        if request.base_url is not None:
            provider.base_url = request.base_url

        if request.model is not None:
            provider.model = request.model

        if request.api_key is not None:
            try:
                provider.api_key_encrypted = encrypt_api_key(request.api_key)
            except Exception as exc:
                raise ApiException(
                    status_code=500, code=50001, message="AI_PROVIDER_FERNET_KEY not configured"
                ) from exc
            provider.api_key_hint = api_key_hint(request.api_key)

        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise ApiException(
                status_code=409, code=40900, message="Update failed due to constraint violation"
            ) from exc

        self.db.refresh(provider)
        return provider

    def delete(self, id: UUID) -> None:
        provider = self.find_by_id(id)
        self.db.delete(provider)
        self.db.commit()

    def activate(self, id: UUID) -> AiProvider:
        provider = self.find_by_id(id)

        # Deactivate all other providers first
        self.db.query(AiProvider).filter(
            AiProvider.is_active.is_(True),
            AiProvider.id != provider.id,
        ).update({AiProvider.is_active: False}, synchronize_session=False)

        provider.is_active = True

        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise ApiException(
                status_code=409, code=40901, message="Another provider is already active"
            ) from exc

        self.db.refresh(provider)
        return provider

    def test_connection(self, id: UUID) -> AiProviderTestConnectionResponse:
        provider = self.find_by_id(id)

        try:
            api_key = decrypt_api_key(provider.api_key_encrypted)
        except Exception:
            return AiProviderTestConnectionResponse(
                ok=False, status_code=None, message="Failed to decrypt API key"
            )

        base = provider.base_url.rstrip("/")
        if not base.endswith("/v1"):
            base += "/v1"
        url = base + "/models"
        req = Request(
            url,
            headers={
                "content-type": "application/json",
                "authorization": f"Bearer {api_key}",
            },
            method="GET",
        )

        try:
            with urlopen(req, timeout=10) as resp:
                status = getattr(resp, "status", None) or resp.getcode()
                ok = status is not None and 200 <= int(status) < 300
                return AiProviderTestConnectionResponse(
                    ok=ok,
                    status_code=int(status) if status else None,
                    message="OK" if ok else "Request failed",
                )
        except HTTPError as exc:
            # 尝试读取响应体中的错误信息
            error_body = ""
            try:
                error_body = exc.read().decode("utf-8", errors="ignore")
            except Exception:
                pass
            message = f"HTTP {exc.code}: {exc.reason}"
            if error_body:
                message += f" - {error_body[:200]}"
            return AiProviderTestConnectionResponse(
                ok=False, status_code=exc.code, message=message
            )
        except URLError as exc:
            message = f"Connection failed: {exc.reason}"
            return AiProviderTestConnectionResponse(
                ok=False, status_code=None, message=message
            )
        except Exception as exc:
            message = f"Connection failed: {type(exc).__name__}: {str(exc)}"
            return AiProviderTestConnectionResponse(
                ok=False, status_code=None, message=message
            )

    @staticmethod
    def fetch_models(request: FetchModelsRequest) -> FetchModelsResponse:
        """Fetch available models from the AI provider."""
        import json

        base = request.base_url.rstrip("/")
        if not base.endswith("/v1"):
            base += "/v1"
        url = base + "/models"

        return AiProviderService._do_fetch_models(url, request.api_key)

    def fetch_models_by_id(self, id: UUID) -> FetchModelsResponse:
        """Fetch available models using stored API key."""
        provider = self.find_by_id(id)

        try:
            api_key = decrypt_api_key(provider.api_key_encrypted)
        except Exception:
            return FetchModelsResponse(ok=False, message="Failed to decrypt API key")

        base = provider.base_url.rstrip("/")
        if not base.endswith("/v1"):
            base += "/v1"
        url = base + "/models"

        return AiProviderService._do_fetch_models(url, api_key)

    @staticmethod
    def _do_fetch_models(url: str, api_key: str) -> FetchModelsResponse:
        """Internal method to fetch models from URL."""
        import json

        req = Request(
            url,
            headers={
                "content-type": "application/json",
                "authorization": f"Bearer {api_key}",
            },
            method="GET",
        )

        try:
            with urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                models = []
                if "data" in data and isinstance(data["data"], list):
                    for item in data["data"]:
                        if isinstance(item, dict) and "id" in item:
                            models.append(item["id"])
                models.sort()
                return FetchModelsResponse(ok=True, models=models)
        except HTTPError as exc:
            error_body = ""
            try:
                error_body = exc.read().decode("utf-8", errors="ignore")
            except Exception:
                pass
            message = f"HTTP {exc.code}: {exc.reason}"
            if error_body:
                message += f" - {error_body[:200]}"
            return FetchModelsResponse(ok=False, message=message)
        except URLError as exc:
            return FetchModelsResponse(ok=False, message=f"Connection failed: {exc.reason}")
        except Exception as exc:
            return FetchModelsResponse(ok=False, message=f"Error: {type(exc).__name__}: {str(exc)}")
