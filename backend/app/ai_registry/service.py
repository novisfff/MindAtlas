from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse
from uuid import UUID
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.ai_provider.crypto import api_key_hint, decrypt_api_key, encrypt_api_key
from app.ai_registry.models import AiComponentBinding, AiCredential, AiModel
from app.ai_registry.schemas import AiComponent, AiModelType, DiscoveredModel
from app.common.exceptions import ApiException


# Blocked IP ranges for SSRF protection
_BLOCKED_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),      # Loopback
    ipaddress.ip_network("10.0.0.0/8"),       # Private
    ipaddress.ip_network("172.16.0.0/12"),    # Private
    ipaddress.ip_network("192.168.0.0/16"),   # Private
    ipaddress.ip_network("169.254.0.0/16"),   # Link-local / AWS metadata
    ipaddress.ip_network("::1/128"),          # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),         # IPv6 private
    ipaddress.ip_network("fe80::/10"),        # IPv6 link-local
]

_OPENAI_COMPAT_DEFAULT_HEADERS = {
    "Content-type": "application/json",
    "accept": "application/json",
    "user-agent": "MindAtlas/1.0",
}


def _validate_base_url(base_url: str) -> None:
    """Validate base_url to prevent SSRF attacks."""
    if not base_url:
        raise ApiException(status_code=400, code=40020, message="base_url is required")

    parsed = urlparse(base_url)

    # Only allow http/https schemes
    if parsed.scheme not in ("http", "https"):
        raise ApiException(status_code=400, code=40021, message="base_url must use http or https scheme")

    hostname = parsed.hostname
    if not hostname:
        raise ApiException(status_code=400, code=40022, message="Invalid base_url: no hostname")

    # Block localhost variants
    if hostname.lower() in ("localhost", "localhost.localdomain"):
        raise ApiException(status_code=400, code=40023, message="base_url cannot point to localhost")

    # Resolve hostname and check IP
    try:
        addrs = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        for family, _, _, _, sockaddr in addrs:
            ip_str = sockaddr[0]
            ip = ipaddress.ip_address(ip_str)
            for net in _BLOCKED_NETWORKS:
                if ip in net:
                    raise ApiException(status_code=400, code=40024, message=f"base_url resolves to blocked IP range")
    except socket.gaierror:
        # DNS resolution failed - allow it (might be valid external host)
        pass
    except ApiException:
        raise
    except Exception:
        pass


def _normalize_base_url(base_url: str) -> str:
    """规范化 base_url, 确保以 /v1 结尾"""
    base = (base_url or "").rstrip("/")
    if not base.endswith("/v1"):
        base += "/v1"
    return base


def _infer_model_type(model_id: str) -> AiModelType:
    """根据模型名称推断类型"""
    value = (model_id or "").lower()
    if "embedding" in value or value.startswith("text-embedding-") or value.startswith("embed-"):
        return "embedding"
    return "llm"


def _build_openai_compat_headers(api_key: str) -> dict[str, str]:
    return {
        **_OPENAI_COMPAT_DEFAULT_HEADERS,
        "Authorization": f"Bearer {(api_key or '').strip()}",
    }


class AiCredentialService:
    """AI 凭据服务"""

    def __init__(self, db: Session):
        self.db = db

    def find_all(self) -> list[AiCredential]:
        return self.db.query(AiCredential).order_by(AiCredential.created_at.desc()).all()

    def find_by_id(self, id: UUID) -> AiCredential:
        cred = self.db.query(AiCredential).filter(AiCredential.id == id).first()
        if not cred:
            raise ApiException(status_code=404, code=40400, message=f"AiCredential not found: {id}")
        return cred

    def create(self, name: str, base_url: str, api_key: str) -> AiCredential:
        _validate_base_url(base_url)

        existing = self.db.query(AiCredential).filter(AiCredential.name.ilike(name)).first()
        if existing:
            raise ApiException(status_code=400, code=40001, message=f"AiCredential name already exists: {name}")

        try:
            encrypted = encrypt_api_key(api_key)
        except Exception as exc:
            raise ApiException(status_code=500, code=50001, message="AI_PROVIDER_FERNET_KEY not configured") from exc

        cred = AiCredential(
            name=name,
            base_url=base_url,
            api_key_encrypted=encrypted,
            api_key_hint=api_key_hint(api_key),
        )
        self.db.add(cred)
        self.db.commit()
        self.db.refresh(cred)
        return cred

    def update(self, id: UUID, *, name: str | None, base_url: str | None, api_key: str | None) -> AiCredential:
        cred = self.find_by_id(id)

        if name is not None and cred.name.lower() != name.lower():
            existing = self.db.query(AiCredential).filter(AiCredential.name.ilike(name)).first()
            if existing:
                raise ApiException(status_code=400, code=40001, message=f"AiCredential name already exists: {name}")
            cred.name = name

        if base_url is not None:
            _validate_base_url(base_url)
            cred.base_url = base_url

        if api_key is not None:
            try:
                cred.api_key_encrypted = encrypt_api_key(api_key)
            except Exception as exc:
                raise ApiException(status_code=500, code=50001, message="AI_PROVIDER_FERNET_KEY not configured") from exc
            cred.api_key_hint = api_key_hint(api_key)

        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise ApiException(status_code=409, code=40900, message="Update failed due to constraint violation") from exc
        self.db.refresh(cred)
        return cred

    def delete(self, id: UUID) -> None:
        cred = self.find_by_id(id)

        # 检查是否有模型被绑定使用
        model_ids = [m.id for m in self.db.query(AiModel.id).filter(AiModel.credential_id == cred.id).all()]
        if model_ids:
            in_use = (
                self.db.query(AiComponentBinding)
                .filter(
                    (AiComponentBinding.llm_model_id.in_(model_ids))
                    | (AiComponentBinding.embedding_model_id.in_(model_ids))
                )
                .first()
            )
            if in_use:
                raise ApiException(status_code=409, code=40910, message="Credential is in use by model bindings")

        self.db.delete(cred)
        self.db.commit()

    def test_connection(self, id: UUID) -> tuple[bool, int | None, str]:
        cred = self.find_by_id(id)
        try:
            api_key = decrypt_api_key(cred.api_key_encrypted)
        except Exception:
            return False, None, "Failed to decrypt API key"

        url = _normalize_base_url(cred.base_url) + "/models"
        req = Request(
            url,
            headers=_build_openai_compat_headers(api_key),
            method="GET",
        )
        try:
            with urlopen(req, timeout=10) as resp:
                status = getattr(resp, "status", None) or resp.getcode()
                ok = status is not None and 200 <= int(status) < 300
                return ok, int(status) if status else None, "OK" if ok else "Request failed"
        except HTTPError as exc:
            error_body = ""
            try:
                error_body = exc.read().decode("utf-8", errors="ignore")
            except Exception:
                pass
            msg = f"HTTP {exc.code}: {exc.reason}"
            if error_body:
                msg += f" - {error_body[:200]}"
            return False, exc.code, msg
        except URLError as exc:
            return False, None, f"Connection failed: {exc.reason}"
        except Exception as exc:
            return False, None, f"Connection failed: {type(exc).__name__}: {str(exc)}"

    @staticmethod
    def discover_models_by_key(*, base_url: str, api_key: str) -> tuple[bool, list[DiscoveredModel], str | None]:
        """通过 API Key 发现可用模型"""
        import json

        _validate_base_url(base_url)

        url = _normalize_base_url(base_url) + "/models"
        req = Request(
            url,
            headers=_build_openai_compat_headers(api_key),
            method="GET",
        )

        try:
            with urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                models: list[str] = []
                if "data" in data and isinstance(data["data"], list):
                    for item in data["data"]:
                        if isinstance(item, dict) and "id" in item:
                            models.append(str(item["id"]))
                models.sort()
                out = [DiscoveredModel(name=m, suggested_type=_infer_model_type(m)) for m in models]
                return True, out, None
        except HTTPError as exc:
            error_body = ""
            try:
                error_body = exc.read().decode("utf-8", errors="ignore")
            except Exception:
                pass
            msg = f"HTTP {exc.code}: {exc.reason}"
            if error_body:
                msg += f" - {error_body[:200]}"
            return False, [], msg
        except URLError as exc:
            return False, [], f"Connection failed: {exc.reason}"
        except Exception as exc:
            return False, [], f"Error: {type(exc).__name__}: {str(exc)}"

    def discover_models_by_id(self, id: UUID) -> tuple[bool, list[DiscoveredModel], str | None]:
        """通过凭据 ID 发现可用模型"""
        cred = self.find_by_id(id)
        try:
            api_key = decrypt_api_key(cred.api_key_encrypted)
        except Exception:
            return False, [], "Failed to decrypt API key"
        return self.discover_models_by_key(base_url=cred.base_url, api_key=api_key)


class AiModelService:
    """AI 模型服务"""

    def __init__(self, db: Session):
        self.db = db

    def find_all(self, *, credential_id: UUID | None = None, model_type: AiModelType | None = None) -> list[AiModel]:
        q = self.db.query(AiModel)
        if credential_id is not None:
            q = q.filter(AiModel.credential_id == credential_id)
        if model_type is not None:
            q = q.filter(AiModel.model_type == model_type)
        return q.order_by(AiModel.created_at.desc()).all()

    def find_by_id(self, id: UUID) -> AiModel:
        m = self.db.query(AiModel).filter(AiModel.id == id).first()
        if not m:
            raise ApiException(status_code=404, code=40400, message=f"AiModel not found: {id}")
        return m

    def create(self, *, credential_id: UUID, name: str, model_type: AiModelType) -> AiModel:
        cred = self.db.query(AiCredential).filter(AiCredential.id == credential_id).first()
        if not cred:
            raise ApiException(status_code=404, code=40400, message=f"AiCredential not found: {credential_id}")

        m = AiModel(credential_id=credential_id, name=name, model_type=model_type)
        self.db.add(m)
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise ApiException(status_code=409, code=40900, message="Model already exists for this credential") from exc
        self.db.refresh(m)
        return m

    def update(self, id: UUID, *, name: str | None, model_type: AiModelType | None) -> AiModel:
        m = self.find_by_id(id)
        if name is not None:
            m.name = name
        if model_type is not None:
            m.model_type = model_type
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise ApiException(status_code=409, code=40900, message="Update failed due to constraint violation") from exc
        self.db.refresh(m)
        return m

    def delete(self, id: UUID) -> None:
        m = self.find_by_id(id)
        in_use = (
            self.db.query(AiComponentBinding)
            .filter((AiComponentBinding.llm_model_id == m.id) | (AiComponentBinding.embedding_model_id == m.id))
            .first()
        )
        if in_use:
            raise ApiException(status_code=409, code=40911, message="Model is in use by component bindings")
        self.db.delete(m)
        self.db.commit()


class AiBindingService:
    """AI 组件绑定服务"""

    def __init__(self, db: Session):
        self.db = db

    def _get_or_create(self, component: AiComponent) -> AiComponentBinding:
        row = (
            self.db.query(AiComponentBinding)
            .filter(AiComponentBinding.component == component)
            .first()
        )
        if row:
            return row
        row = AiComponentBinding(component=component, llm_model_id=None, embedding_model_id=None)
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def get_bindings(self) -> dict[str, AiComponentBinding]:
        return {
            "assistant": self._get_or_create("assistant"),
            "lightrag": self._get_or_create("lightrag"),
        }

    def _validate_model_type(self, model_id: UUID, expected: AiModelType) -> None:
        m = self.db.query(AiModel).filter(AiModel.id == model_id).first()
        if not m:
            raise ApiException(status_code=404, code=40400, message=f"AiModel not found: {model_id}")
        if (m.model_type or "").strip() != expected:
            raise ApiException(status_code=400, code=40010, message=f"Model type mismatch: expected {expected}")

    def update_component(
        self,
        component: AiComponent,
        *,
        llm_model_id: UUID | None,
        embedding_model_id: UUID | None,
    ) -> AiComponentBinding:
        row = self._get_or_create(component)

        if llm_model_id is not None:
            self._validate_model_type(llm_model_id, "llm")
        if embedding_model_id is not None:
            self._validate_model_type(embedding_model_id, "embedding")

        row.llm_model_id = llm_model_id
        row.embedding_model_id = embedding_model_id

        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise ApiException(status_code=409, code=40900, message="Update failed due to constraint violation") from exc
        self.db.refresh(row)
        return row
