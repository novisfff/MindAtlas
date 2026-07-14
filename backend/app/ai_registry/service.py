from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Literal
from uuid import UUID
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from sqlalchemy import desc
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.ai_provider.crypto import api_key_hint, decrypt_api_key, encrypt_api_key
from app.ai_registry.models import (
    AiComponentBinding,
    AiCredential,
    AiModel,
    AiModelCapabilityProbe,
)
from app.ai_registry.runtime import (
    invalidate_model_probe_pointers,
    lock_credential,
    lock_model_with_credential,
    lock_models_for_credential_sorted,
)
from app.ai_registry.schemas import AiComponent, AiModelType, DiscoveredModel
from app.common.exceptions import ApiException
from app.common.ssrf import normalize_openai_base_url, validate_url_ssrf
from app.config import get_settings

_OPENAI_COMPAT_DEFAULT_HEADERS = {
    "content-type": "application/json",
    "accept": "application/json",
    "user-agent": "MindAtlas/1.0",
}
_AI_COMPONENTS: tuple[AiComponent, ...] = ("assistant", "lightrag", "workflow_copilot")

PromotionOutcome = Literal["promoted", "not_requested", "config_changed"]

# Per-process single-flight for paid live probes (cost safeguard only).
_PROBE_FLIGHT_LOCK = threading.Lock()
_PROBE_IN_FLIGHT: set[UUID] = set()


def _credential_runtime_snapshot(cred: AiCredential) -> dict[str, Any]:
    """Plan 01 credential execution snapshot with normalized base URL semantics."""
    from app.assistant.skills.resolution import credential_runtime_sensitive_payload

    payload = credential_runtime_sensitive_payload(cred)
    payload["base_url"] = normalize_openai_base_url(str(payload.get("base_url") or ""))
    return payload


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
        # Canonical lock order: credential first, then associated models sorted by id.
        cred = lock_credential(self.db, id)
        if not cred:
            raise ApiException(status_code=404, code=40400, message=f"AiCredential not found: {id}")

        before = _credential_runtime_snapshot(cred)

        if name is not None and cred.name.lower() != name.lower():
            existing = self.db.query(AiCredential).filter(AiCredential.name.ilike(name)).first()
            if existing:
                raise ApiException(status_code=400, code=40001, message=f"AiCredential name already exists: {name}")
            cred.name = name

        if base_url is not None:
            validate_url_ssrf(base_url, raise_api_exception=True)
            cred.base_url = base_url

        if api_key is not None:
            # Any supplied API-key replacement is treated as execution-sensitive
            # (Fernet ciphertext always changes).
            try:
                cred.api_key_encrypted = encrypt_api_key(api_key)
            except Exception as exc:
                raise ApiException(status_code=500, code=50001, message="AI_PROVIDER_FERNET_KEY not configured") from exc
            cred.api_key_hint = api_key_hint(api_key)

        after = _credential_runtime_snapshot(cred)
        runtime_sensitive_changed = before != after
        if runtime_sensitive_changed:
            # Lock models in sorted order inside the same transaction before mutating revision/pointers.
            models = lock_models_for_credential_sorted(self.db, cred.id)
            cred.runtime_revision = int(cred.runtime_revision or 1) + 1
            invalidate_model_probe_pointers(models)

        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise ApiException(status_code=409, code=40900, message="Update failed due to constraint violation") from exc
        self.db.refresh(cred)
        # Credential repair (base URL / API key) may unblock shadow publication.
        if runtime_sensitive_changed:
            try:
                from app.assistant.skills.legacy_adapter import best_effort_sync_all

                best_effort_sync_all(self.db)
            except Exception:
                pass
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

        # Canonical lock order: credential then model.
        _cred, m = lock_model_with_credential(self.db, id)
        if not m:
            raise ApiException(status_code=404, code=40400, message=f"AiModel not found: {id}")
        before = model_runtime_sensitive_payload(m)
        if name is not None:
            m.name = name
        if model_type is not None:
            m.model_type = model_type
        after = model_runtime_sensitive_payload(m)
        runtime_sensitive_changed = before != after
        if runtime_sensitive_changed:
            m.runtime_revision = int(m.runtime_revision or 1) + 1
            invalidate_model_probe_pointers([m])
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise ApiException(status_code=409, code=40900, message="Update failed due to constraint violation") from exc
        self.db.refresh(m)
        # Model repair (name / type) may unblock shadow publication.
        if runtime_sensitive_changed:
            try:
                from app.assistant.skills.legacy_adapter import best_effort_sync_all

                best_effort_sync_all(self.db)
            except Exception:
                pass
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
        # Default-model binding repair: reconcile unresolved shadow publications.
        if component == "assistant":
            try:
                from app.assistant.skills.legacy_adapter import best_effort_sync_all

                best_effort_sync_all(self.db)
            except Exception:
                pass
        return row


@dataclass(frozen=True)
class LiveProbeResult:
    """Persisted probe evidence plus promotion metadata for API responses."""

    probe: AiModelCapabilityProbe
    promotion_outcome: PromotionOutcome
    is_current: bool
    is_stale_for_current_config: bool


@dataclass(frozen=True)
class _ProbeConfigSnapshot:
    model_id: UUID
    model_name: str
    model_type: str
    model_runtime_revision: int
    credential_id: UUID
    credential_runtime_revision: int
    base_url: str
    model_config_digest: str
    endpoint_identity: dict[str, Any]
    adapter_key: str
    adapter_revision: str
    app_build_revision: str
    # Ciphertext frozen with the same lock as base_url. Never re-query the live
    # credential row for the key after unlock — that can pair a new secret with
    # an old endpoint (or vice versa) across concurrent credential updates.
    api_key_encrypted: str = field(repr=False, compare=False)


class AiModelCapabilityProbeService:
    """Persist immutable capability probe evidence and manage the current pointer.

    There is intentionally no update/delete method. Probe rows are append-only.
    """

    def __init__(
        self,
        db: Session,
        *,
        enabled: bool | None = None,
        provider_runner: Callable[..., Any] | None = None,
        app_build_revision: str | None = None,
        adapter_revision: str | None = None,
    ):
        self.db = db
        settings = get_settings()
        self.enabled = (
            bool(settings.ai_model_capability_probe_enabled)
            if enabled is None
            else bool(enabled)
        )
        self._provider_runner = provider_runner
        self._app_build_revision = (
            app_build_revision
            if app_build_revision is not None
            else str(settings.app_build_revision or "development")
        )
        if adapter_revision is not None:
            self._adapter_revision = adapter_revision
        else:
            from app.assistant.provider_loop.adapters.openai_chat import (
                DEFAULT_ADAPTER_REVISION,
            )

            self._adapter_revision = DEFAULT_ADAPTER_REVISION

    def list_for_model(
        self,
        model_id: UUID,
        *,
        limit: int = 20,
        offset: int = 0,
    ) -> list[tuple[AiModelCapabilityProbe, bool, bool]]:
        """Return newest-first history with (probe, is_current, is_stale) markers."""
        model = self.db.query(AiModel).filter(AiModel.id == model_id).first()
        if not model:
            raise ApiException(status_code=404, code=40400, message=f"AiModel not found: {model_id}")

        current_digest = self._try_current_config_digest(model)
        rows = (
            self.db.query(AiModelCapabilityProbe)
            .filter(AiModelCapabilityProbe.model_id == model_id)
            .order_by(
                desc(AiModelCapabilityProbe.created_at),
                desc(AiModelCapabilityProbe.id),
            )
            .offset(offset)
            .limit(limit)
            .all()
        )
        out: list[tuple[AiModelCapabilityProbe, bool, bool]] = []
        for row in rows:
            is_current = model.current_capability_probe_id == row.id
            is_stale = (
                current_digest is None
                or row.model_config_digest != current_digest
            )
            out.append((row, is_current, is_stale))
        return out

    def run_live_probe(
        self,
        model_id: UUID,
        *,
        adapter_key: str,
        confirm_provider_call: bool,
        promote: bool = True,
    ) -> LiveProbeResult:
        """Run one explicit live probe, persist evidence, optionally promote."""
        from app.assistant.provider_loop.adapters.openai_chat import ADAPTER_KEY
        from app.assistant.provider_loop.probe import (
            ModelCapabilityProbeEvidence,
            PROBE_CONTRACT_VERSION,
            build_endpoint_identity,
            build_model_config_digest,
            run_model_capability_probe,
        )
        from app.assistant.domain.contracts import create_model_ref, create_provider_ref
        from app.assistant.provider_loop.adapters.openai_chat import (
            ExactOpenAIChatRuntimeConfig,
            OpenAIChatCompletionsAdapter,
        )
        from app.assistant.provider_loop.aliases import OPENAI_CHAT_PROVIDER_PROTOCOL

        # 1. Gate / confirmation / adapter / model type — before decrypt/Provider.
        if not self.enabled:
            raise ApiException(
                status_code=403,
                code=40380,
                message="Live model capability probe is disabled",
            )
        if not confirm_provider_call:
            raise ApiException(
                status_code=400,
                code=40080,
                message="confirmProviderCall=true is required for live probing",
            )
        if (adapter_key or "").strip() != ADAPTER_KEY:
            raise ApiException(
                status_code=400,
                code=40081,
                message=f"Unsupported adapterKey; only {ADAPTER_KEY} is available",
            )

        model = self.db.query(AiModel).filter(AiModel.id == model_id).first()
        if not model:
            raise ApiException(status_code=404, code=40400, message=f"AiModel not found: {model_id}")
        if (model.model_type or "").strip() != "llm":
            raise ApiException(
                status_code=400,
                code=40082,
                message="Capability probe is only supported for llm models",
            )
        cred = (
            self.db.query(AiCredential)
            .filter(AiCredential.id == model.credential_id)
            .first()
        )
        if not cred:
            raise ApiException(
                status_code=404,
                code=40400,
                message=f"AiCredential not found for model: {model_id}",
            )

        # Per-model single-flight (process-local cost safeguard).
        with _PROBE_FLIGHT_LOCK:
            if model_id in _PROBE_IN_FLIGHT:
                raise ApiException(
                    status_code=409,
                    code=40980,
                    message="Capability probe already running for this model",
                )
            _PROBE_IN_FLIGHT.add(model_id)

        try:
            # 2. Snapshot runtime config under short lock; release before Provider I/O.
            snapshot = self._snapshot_locked_config(
                model_id=model_id,
                adapter_key=ADAPTER_KEY,
            )

            # 3. Secret-free endpoint revalidation + SSRF before decrypt.
            try:
                endpoint_identity = build_endpoint_identity(snapshot.base_url)
            except Exception as exc:
                raise ApiException(
                    status_code=400,
                    code=40083,
                    message="Provider base URL rejected (user-info/query/fragment or invalid endpoint)",
                ) from exc
            if endpoint_identity != snapshot.endpoint_identity:
                raise ApiException(
                    status_code=409,
                    code=40981,
                    message="Provider endpoint identity drift before probe",
                )
            try:
                validate_url_ssrf(
                    normalize_openai_base_url(snapshot.base_url),
                    raise_api_exception=True,
                )
            except ApiException:
                raise
            except Exception as exc:
                raise ApiException(
                    status_code=400,
                    code=40023,
                    message="Provider base URL failed SSRF validation",
                ) from exc

            # Decrypt only the ciphertext frozen with the endpoint under the same lock.
            try:
                api_key = decrypt_api_key(snapshot.api_key_encrypted)
            except Exception as exc:
                raise ApiException(
                    status_code=500,
                    code=50002,
                    message="Failed to decrypt API key",
                ) from exc
            api_key = (api_key or "").strip()
            if not api_key:
                raise ApiException(
                    status_code=500,
                    code=50002,
                    message="Failed to decrypt API key",
                )

            # 4. Run bounded probe outside a long DB transaction.
            if self._provider_runner is not None:
                evidence = self._provider_runner(
                    snapshot=snapshot,
                    api_key=api_key,
                )
            else:
                runtime_config = ExactOpenAIChatRuntimeConfig(
                    model_id=snapshot.model_id,
                    model_name=snapshot.model_name,
                    model_type=snapshot.model_type,
                    model_runtime_revision=snapshot.model_runtime_revision,
                    credential_id=snapshot.credential_id,
                    credential_runtime_revision=snapshot.credential_runtime_revision,
                    model_config_digest=snapshot.model_config_digest,
                    adapter_key=snapshot.adapter_key,
                    adapter_revision=snapshot.adapter_revision,
                    app_build_revision=snapshot.app_build_revision,
                    base_url=snapshot.base_url,
                    api_key=api_key,
                    endpoint_identity=snapshot.endpoint_identity,
                )
                provider = OpenAIChatCompletionsAdapter(runtime_config=runtime_config)
                provider_ref = create_provider_ref(
                    provider_protocol=OPENAI_CHAT_PROVIDER_PROTOCOL,
                    provider_config_id=snapshot.credential_id,
                    provider_runtime_revision=snapshot.credential_runtime_revision,
                    provider_config_digest=None,
                    adapter_key=snapshot.adapter_key,
                    adapter_revision=snapshot.adapter_revision,
                    protocol_revision=None,
                    app_build_revision=snapshot.app_build_revision,
                )
                model_ref = create_model_ref(
                    model_id=snapshot.model_id,
                    model_name=snapshot.model_name,
                    model_type=snapshot.model_type,  # type: ignore[arg-type]
                    model_runtime_revision=snapshot.model_runtime_revision,
                    credential_id=snapshot.credential_id,
                    credential_runtime_revision=snapshot.credential_runtime_revision,
                    credential_config_digest=None,
                    model_config_digest=snapshot.model_config_digest,
                    provider_ref_digest=provider_ref.provider_ref_digest,
                    capability_probe_id=None,
                    capability_probe_digest=None,
                )
                evidence = run_model_capability_probe(
                    provider=provider,
                    model_ref=model_ref,
                    app_build_revision=snapshot.app_build_revision,
                )

            if not isinstance(evidence, ModelCapabilityProbeEvidence):
                raise ApiException(
                    status_code=500,
                    code=50080,
                    message="Probe runner returned invalid evidence",
                )

            # 5-7. Re-lock, recompute digest, insert history, optional promote.
            return self._persist_evidence(
                model_id=model_id,
                original_snapshot=snapshot,
                evidence=evidence,
                promote=promote,
            )
        finally:
            with _PROBE_FLIGHT_LOCK:
                _PROBE_IN_FLIGHT.discard(model_id)

    def _snapshot_locked_config(
        self,
        *,
        model_id: UUID,
        adapter_key: str,
    ) -> _ProbeConfigSnapshot:
        from app.assistant.provider_loop.probe import (
            PROBE_CONTRACT_VERSION,
            build_endpoint_identity,
            build_model_config_digest,
        )

        cred, model = lock_model_with_credential(self.db, model_id)
        if not model or not cred:
            self.db.rollback()
            raise ApiException(status_code=404, code=40400, message=f"AiModel not found: {model_id}")

        base_url = normalize_openai_base_url(cred.base_url)
        try:
            endpoint_identity = build_endpoint_identity(base_url)
        except Exception as exc:
            self.db.rollback()
            raise ApiException(
                status_code=400,
                code=40083,
                message="Provider base URL rejected (user-info/query/fragment or invalid endpoint)",
            ) from exc

        digest = build_model_config_digest(
            model_id=model.id,
            model_name=model.name,
            model_type=model.model_type,
            model_runtime_revision=int(model.runtime_revision or 1),
            credential_id=cred.id,
            credential_runtime_revision=int(cred.runtime_revision or 1),
            endpoint_identity=endpoint_identity,
            adapter_key=adapter_key,
            adapter_revision=self._adapter_revision,
            app_build_revision=self._app_build_revision,
        )
        encrypted = str(getattr(cred, "api_key_encrypted", "") or "").strip()
        if not encrypted:
            self.db.rollback()
            raise ApiException(
                status_code=500,
                code=50002,
                message="Failed to decrypt API key",
            )
        snapshot = _ProbeConfigSnapshot(
            model_id=model.id,
            model_name=model.name,
            model_type=model.model_type,
            model_runtime_revision=int(model.runtime_revision or 1),
            credential_id=cred.id,
            credential_runtime_revision=int(cred.runtime_revision or 1),
            base_url=base_url,
            model_config_digest=digest,
            endpoint_identity=dict(endpoint_identity),
            adapter_key=adapter_key,
            adapter_revision=self._adapter_revision,
            app_build_revision=self._app_build_revision,
            api_key_encrypted=encrypted,
        )
        # Release the short snapshot transaction before Provider I/O.
        self.db.commit()
        return snapshot

    def _persist_evidence(
        self,
        *,
        model_id: UUID,
        original_snapshot: _ProbeConfigSnapshot,
        evidence: Any,
        promote: bool,
    ) -> LiveProbeResult:
        from app.assistant.provider_loop.probe import (
            build_endpoint_identity,
            build_model_config_digest,
            observations_payload,
        )

        cred, model = lock_model_with_credential(self.db, model_id)
        if not model or not cred:
            self.db.rollback()
            raise ApiException(status_code=404, code=40400, message=f"AiModel not found: {model_id}")

        base_url = normalize_openai_base_url(cred.base_url)
        try:
            current_endpoint = build_endpoint_identity(base_url)
        except Exception:
            current_endpoint = None
        current_digest = None
        if current_endpoint is not None:
            current_digest = build_model_config_digest(
                model_id=model.id,
                model_name=model.name,
                model_type=model.model_type,
                model_runtime_revision=int(model.runtime_revision or 1),
                credential_id=cred.id,
                credential_runtime_revision=int(cred.runtime_revision or 1),
                endpoint_identity=current_endpoint,
                adapter_key=original_snapshot.adapter_key,
                adapter_revision=original_snapshot.adapter_revision,
                app_build_revision=original_snapshot.app_build_revision,
            )

        # Config identity is recomputed from locked rows; also compare the
        # original snapshot fields so mid-flight execution changes are exact.
        field_changed = (
            int(model.runtime_revision or 1) != int(original_snapshot.model_runtime_revision)
            or int(cred.runtime_revision or 1) != int(original_snapshot.credential_runtime_revision)
            or (model.name or "") != original_snapshot.model_name
            or (model.model_type or "") != original_snapshot.model_type
            or base_url != original_snapshot.base_url
            or model.credential_id != original_snapshot.credential_id
        )
        digest_changed = (
            current_digest is None
            or current_digest != original_snapshot.model_config_digest
        )
        config_changed = field_changed or digest_changed

        # Always persist against the original snapshot config digest.
        capabilities_json = observations_payload(evidence.capabilities)
        probe = AiModelCapabilityProbe(
            model_id=model_id,
            probe_contract_version=int(evidence.probe_contract_version),
            adapter_key=evidence.adapter_key,
            adapter_revision=evidence.adapter_revision,
            model_config_digest=original_snapshot.model_config_digest,
            status=evidence.status,
            capabilities=capabilities_json,
            probe_digest=evidence.probe_digest,
            safe_error_code=evidence.safe_error_code,
            safe_error_summary=evidence.safe_error_summary,
        )
        self.db.add(probe)
        self.db.flush()

        if config_changed:
            outcome: PromotionOutcome = "config_changed"
        elif not promote:
            outcome = "not_requested"
        else:
            model.current_capability_probe_id = probe.id
            outcome = "promoted"
            # Promotion must not bump model runtime revision.

        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise ApiException(
                status_code=409,
                code=40900,
                message="Failed to persist capability probe evidence",
            ) from exc
        self.db.refresh(probe)
        self.db.refresh(model)

        is_current = model.current_capability_probe_id == probe.id
        is_stale = (
            current_digest is None
            or probe.model_config_digest != current_digest
        )
        return LiveProbeResult(
            probe=probe,
            promotion_outcome=outcome,
            is_current=is_current,
            is_stale_for_current_config=is_stale,
        )

    def _try_current_config_digest(self, model: AiModel) -> str | None:
        from app.assistant.provider_loop.adapters.openai_chat import ADAPTER_KEY
        from app.assistant.provider_loop.probe import (
            build_endpoint_identity,
            build_model_config_digest,
        )

        cred = (
            self.db.query(AiCredential)
            .filter(AiCredential.id == model.credential_id)
            .first()
        )
        if not cred:
            return None
        try:
            endpoint = build_endpoint_identity(normalize_openai_base_url(cred.base_url))
        except Exception:
            return None
        return build_model_config_digest(
            model_id=model.id,
            model_name=model.name,
            model_type=model.model_type,
            model_runtime_revision=int(model.runtime_revision or 1),
            credential_id=cred.id,
            credential_runtime_revision=int(cred.runtime_revision or 1),
            endpoint_identity=endpoint,
            adapter_key=ADAPTER_KEY,
            adapter_revision=self._adapter_revision,
            app_build_revision=self._app_build_revision,
        )
