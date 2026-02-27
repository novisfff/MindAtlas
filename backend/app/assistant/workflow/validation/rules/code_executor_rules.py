from __future__ import annotations

from app.assistant.workflow.validation.contracts import (
    _CODE_EXECUTOR_ENTRYPOINT_RE,
    _CODE_EXECUTOR_LANGUAGES,
)
from app.assistant.workflow.validation.helpers import (
    cfg_get,
    validate_code_executor_imports,
    validate_code_executor_input_bindings,
    validate_code_executor_signature,
    validate_output_fields_config,
)
from app.assistant.workflow.validation.models import ValidationError

def validate_code_executor_node_config(
    *,
    node_id: str,
    cfg: dict,
    errors: list[ValidationError],
    subject: str,
    validate_timeout: bool,
) -> None:
    language_raw = cfg_get(cfg, "language", default="python")
    language = str(language_raw or "python").strip().lower()
    if language not in _CODE_EXECUTOR_LANGUAGES:
        errors.append(
            ValidationError(
                node_id=node_id,
                message=f"{subject} language is invalid: {language_raw}",
            )
        )

    code_text = cfg_get(cfg, "code", default="")
    if not isinstance(code_text, str) or not code_text.strip():
        errors.append(
            ValidationError(
                node_id=node_id,
                message=f"{subject} code is required and must be a string",
            )
        )
    elif language in _CODE_EXECUTOR_LANGUAGES:
        validate_code_executor_imports(
            node_id=node_id,
            language=language,
            code_text=code_text,
            errors=errors,
        )

    entrypoint_raw = cfg_get(cfg, "entrypoint", default="main")
    entrypoint = str(entrypoint_raw or "main").strip() or "main"
    if not _CODE_EXECUTOR_ENTRYPOINT_RE.fullmatch(entrypoint):
        errors.append(
            ValidationError(
                node_id=node_id,
                message=f"{subject} entrypoint is invalid: {entrypoint_raw}",
            )
        )

    validated_binding_keys = validate_code_executor_input_bindings(
        node_id=node_id,
        subject=subject,
        input_bindings=cfg_get(cfg, "input_bindings", "inputBindings", default=None),
        errors=errors,
    )

    if isinstance(code_text, str) and code_text.strip() and language in _CODE_EXECUTOR_LANGUAGES:
        validate_code_executor_signature(
            node_id=node_id,
            subject=subject,
            language=language,
            entrypoint=entrypoint,
            code_text=code_text,
            expected_params=validated_binding_keys,
            errors=errors,
        )

    if validate_timeout:
        timeout_raw = cfg_get(cfg, "timeout_ms", "timeoutMs", default=None)
        if timeout_raw is not None and str(timeout_raw).strip():
            try:
                timeout_ms = int(timeout_raw)
            except Exception:
                timeout_ms = 0
            if timeout_ms < 100 or timeout_ms > 5000:
                errors.append(
                    ValidationError(
                        node_id=node_id,
                        message=f"{subject} timeoutMs must be between 100 and 5000",
                    )
                )

    validate_output_fields_config(
        node_id=node_id,
        node_type=subject,
        output_fields=cfg_get(cfg, "output_fields", "outputFields", default=None),
        errors=errors,
        required=True,
    )
