from __future__ import annotations

from uuid import UUID
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.ai_provider.crypto import api_key_hint, decrypt_api_key, encrypt_api_key
from app.ai_registry.models import AiComponentBinding, AiCredential, AiModel
from app.ai_registry.schemas import AiComponent, AiModelType, DiscoveredModel
from app.common.exceptions import ApiException
from app.common.ssrf import normalize_openai_base_url, validate_url_ssrf

_OPENAI_COMPAT_DEFAULT_HEADERS = {
    "content-type": "application/json",
    "accept": "application/json",
    "user-agent": "MindAtlas/1.0",
}
_AI_COMPONENTS: tuple[AiComponent, ...] = ("assistant", "lightrag", "workflow_copilot")


def _infer_model_type(model_id: str) -> AiModelType:
    """根据模型名称推断类型"""
    value = (model_id or "").lower()
    if "embedding" in value or value.startswith("text-embedding-") or value.startswith("embed-"):
        return "embedding"
    return "llm"


def _build_openai_compat_headers(api_key: str) -> dict[str, str]:
    return {
        **_OPENAI_COMPAT_DEFAULT_HEADERS,
        "authorization": f"Bearer {(api_key or '').strip()}",
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
        validate_url_ssrf(base_url, raise_api_exception=True)

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
            runtime_revision=1,
        )
        self.db.add(cred)
        self.db.commit()
        self.db.refresh(cred)
        return cred

    def update(self, id: UUID, *, name: str | None, base_url: str | None, api_key: str | None) -> AiCredential:
        from app.assistant.skills.resolution import credential_runtime_sensitive_payload

        cred = (
            self.db.query(AiCredential)
            .filter(AiCredential.id == id)
            .with_for_update()
            .first()
        )
        if not cred:
            raise ApiException(status_code=404, code=40400, message=f"AiCredential not found: {id}")

        before = credential_runtime_sensitive_payload(cred)

        if name is not None and cred.name.lower() != name.lower():
            existing = self.db.query(AiCredential).filter(AiCredential.name.ilike(name)).first()
            if existing:
                raise ApiException(status_code=400, code=40001, message=f"AiCredential name already exists: {name}")
            cred.name = name

        if base_url is not None:
            validate_url_ssrf(base_url, raise_api_exception=True)
            cred.base_url = base_url

        if api_key is not None:
            try:
                cred.api_key_encrypted = encrypt_api_key(api_key)
            except Exception as exc:
                raise ApiException(status_code=500, code=50001, message="AI_PROVIDER_FERNET_KEY not configured") from exc
            cred.api_key_hint = api_key_hint(api_key)

        after = credential_runtime_sensitive_payload(cred)
        if before != after:
            cred.runtime_revision = int(cred.runtime_revision or 1) + 1

        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise ApiException(status_code=409, code=40900, message="Update failed due to constraint violation") from exc
        self.db.refresh(cred)
        return cred

    def delete(self, id: UUID) -> None:
        from app.assistant.skills.resolution import find_skill_refs_for_model, skill_reference_conflict

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
            for model_id in model_ids:
                refs = find_skill_refs_for_model(self.db, model_id)
                if refs:
                    package_id, version_id = refs[0]
                    raise skill_reference_conflict(
                        package_id=package_id,
                        version_id=version_id,
                        message="Credential model is referenced by a published skill dependency",
                    )

        try:
            self.db.delete(cred)
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise ApiException(
                status_code=409,
                code=40994,
                message="Credential is referenced by a published skill dependency",
            ) from exc

    def test_connection(self, id: UUID) -> tuple[bool, int | None, str]:
        cred = self.find_by_id(id)
        try:
            api_key = decrypt_api_key(cred.api_key_encrypted)
        except Exception:
            return False, None, "Failed to decrypt API key"

        url = normalize_openai_base_url(cred.base_url) + "/models"
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

        validate_url_ssrf(base_url, raise_api_exception=True)

        url = normalize_openai_base_url(base_url) + "/models"
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

        m = AiModel(credential_id=credential_id, name=name, model_type=model_type, runtime_revision=1)
        self.db.add(m)
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise ApiException(status_code=409, code=40900, message="Model already exists for this credential") from exc
        self.db.refresh(m)
        return m

    def update(self, id: UUID, *, name: str | None, model_type: AiModelType | None) -> AiModel:
        from app.assistant.skills.resolution import model_runtime_sensitive_payload

        m = (
            self.db.query(AiModel)
            .filter(AiModel.id == id)
            .with_for_update()
            .first()
        )
        if not m:
            raise ApiException(status_code=404, code=40400, message=f"AiModel not found: {id}")
        before = model_runtime_sensitive_payload(m)
        if name is not None:
            m.name = name
        if model_type is not None:
            m.model_type = model_type
        after = model_runtime_sensitive_payload(m)
        if before != after:
            m.runtime_revision = int(m.runtime_revision or 1) + 1
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise ApiException(status_code=409, code=40900, message="Update failed due to constraint violation") from exc
        self.db.refresh(m)
        return m

    def delete(self, id: UUID, *, confirm_bound_bindings: bool = False) -> None:
        from app.assistant.skills.resolution import find_skill_refs_for_model, skill_reference_conflict

        m = self.find_by_id(id)
        refs = find_skill_refs_for_model(self.db, m.id)
        if refs:
            package_id, version_id = refs[0]
            raise skill_reference_conflict(
                package_id=package_id,
                version_id=version_id,
                message="Model is referenced by a published skill dependency",
            )
        bound_components = (
            self.db.query(AiComponentBinding)
            .filter((AiComponentBinding.llm_model_id == m.id) | (AiComponentBinding.embedding_model_id == m.id))
            .all()
        )
        if bound_components and not confirm_bound_bindings:
            raise ApiException(
                status_code=409,
                code=40911,
                message="Model is in use by component bindings",
                details={
                    "action": "confirm_unbind_then_delete",
                    "components": [str(item.component) for item in bound_components],
                },
            )
        (
            self.db.query(AiComponentBinding)
            .filter(AiComponentBinding.llm_model_id == m.id)
            .update({AiComponentBinding.llm_model_id: None}, synchronize_session=False)
        )
        (
            self.db.query(AiComponentBinding)
            .filter(AiComponentBinding.embedding_model_id == m.id)
            .update({AiComponentBinding.embedding_model_id: None}, synchronize_session=False)
        )
        try:
            self.db.delete(m)
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise ApiException(
                status_code=409,
                code=40994,
                message="Model is referenced by a published skill dependency",
            ) from exc


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
        return {component: self._get_or_create(component) for component in _AI_COMPONENTS}

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
