from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import get_settings

_PYTHON_IMPORT_RE = re.compile(r"^\s*import\s+([a-zA-Z0-9_.,\s]+)", re.MULTILINE)
_PYTHON_FROM_IMPORT_RE = re.compile(r"^\s*from\s+([a-zA-Z0-9_\.]+)\s+import\s+", re.MULTILINE)
_JS_IMPORT_RE = re.compile(
    r"(?:^|\n)\s*import\s+(?:[^'\"]+\s+from\s+)?['\"]([^'\"]+)['\"]|"
    r"require\(\s*['\"]([^'\"]+)['\"]\s*\)",
    re.MULTILINE,
)
_JS_DYNAMIC_IMPORT_RE = re.compile(r"import\s*\(")
_ENTRYPOINT_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")

_PYTHON_DEFAULT_ALLOWED_MODULES = {
    "json",
    "re",
    "math",
    "datetime",
    "statistics",
    "itertools",
    "functools",
    "decimal",
    "uuid",
    "base64",
    "hashlib",
    "collections",
}

_JAVASCRIPT_DEFAULT_ALLOWED_MODULES = {
    "path",
    "url",
    "crypto",
    "util",
}


@dataclass
class CodeExecutionResult:
    output: dict[str, Any]
    stdout: str
    stderr: str


class CodeExecutionError(RuntimeError):
    pass


def _csv_to_set(value: str | None, fallback: set[str]) -> set[str]:
    text = str(value or "").strip()
    if not text:
        return set(fallback)
    return {item.strip() for item in text.split(",") if item.strip()}


def get_python_allowed_modules() -> set[str]:
    settings = get_settings()
    return _csv_to_set(
        getattr(settings, "workflow_code_executor_python_allowed_modules", ""),
        _PYTHON_DEFAULT_ALLOWED_MODULES,
    )


def get_javascript_allowed_modules() -> set[str]:
    settings = get_settings()
    return _csv_to_set(
        getattr(settings, "workflow_code_executor_javascript_allowed_modules", ""),
        _JAVASCRIPT_DEFAULT_ALLOWED_MODULES,
    )


def extract_python_imports(code: str) -> set[str]:
    modules: set[str] = set()
    for match in _PYTHON_IMPORT_RE.finditer(code or ""):
        raw = match.group(1)
        for token in raw.split(","):
            module = token.strip().split(" as ", 1)[0].strip().split(".", 1)[0].strip()
            if module:
                modules.add(module)
    for match in _PYTHON_FROM_IMPORT_RE.finditer(code or ""):
        module = match.group(1).strip().split(".", 1)[0].strip()
        if module:
            modules.add(module)
    return modules


def extract_javascript_imports(code: str) -> set[str]:
    modules: set[str] = set()
    for match in _JS_IMPORT_RE.finditer(code or ""):
        module_name = match.group(1) or match.group(2) or ""
        module_name = module_name.strip()
        if not module_name:
            continue
        root_name = module_name.split("/", 1)[0]
        if root_name:
            modules.add(root_name)
    return modules


def has_javascript_dynamic_import(code: str) -> bool:
    return _JS_DYNAMIC_IMPORT_RE.search(code or "") is not None


def _validate_import_whitelist(language: str, code: str) -> None:
    lang = language.strip().lower()
    if lang == "python":
        disallowed = sorted(extract_python_imports(code) - get_python_allowed_modules())
        if disallowed:
            raise CodeExecutionError(f"Python import not allowed: {', '.join(disallowed)}")
        if "__import__(" in code or "importlib.import_module" in code:
            raise CodeExecutionError("Dynamic Python import is not allowed")
        return

    if lang == "javascript":
        if has_javascript_dynamic_import(code):
            raise CodeExecutionError("Dynamic JavaScript import() is not allowed")
        disallowed = sorted(extract_javascript_imports(code) - get_javascript_allowed_modules())
        if disallowed:
            raise CodeExecutionError(f"JavaScript import not allowed: {', '.join(disallowed)}")
        return

    raise CodeExecutionError(f"Unsupported code executor language: {language}")


def _json_size(value: Any) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=False, default=str))
    except Exception as exc:  # pragma: no cover
        raise CodeExecutionError(f"Code executor output is not JSON serializable: {exc}") from exc


def _validate_array_item_type(field_name: str, items: list[Any], items_type: str) -> None:
    for idx, item in enumerate(items):
        if items_type == "string" and not isinstance(item, str):
            raise CodeExecutionError(f"output field '{field_name}' array item {idx} must be string")
        if items_type == "number" and (
            not isinstance(item, (int, float)) or isinstance(item, bool)
        ):
            raise CodeExecutionError(f"output field '{field_name}' array item {idx} must be number")
        if items_type == "integer" and (
            not isinstance(item, int) or isinstance(item, bool)
        ):
            raise CodeExecutionError(f"output field '{field_name}' array item {idx} must be integer")
        if items_type == "boolean" and not isinstance(item, bool):
            raise CodeExecutionError(f"output field '{field_name}' array item {idx} must be boolean")
        if items_type == "object" and not isinstance(item, dict):
            raise CodeExecutionError(f"output field '{field_name}' array item {idx} must be object")


def _validate_output_against_schema(
    output: dict[str, Any],
    output_fields: list[dict[str, Any]],
) -> dict[str, Any]:
    expected_names = [str(item.get("name", "") or "").strip() for item in output_fields]
    expected_names = [name for name in expected_names if name]
    expected_set = set(expected_names)

    actual_set = set(output.keys())
    missing = sorted(expected_set - actual_set)
    extra = sorted(actual_set - expected_set)
    if missing:
        raise CodeExecutionError(f"output missing required fields: {', '.join(missing)}")
    if extra:
        raise CodeExecutionError(f"output contains unexpected fields: {', '.join(extra)}")

    validated: dict[str, Any] = {}
    for spec in output_fields:
        name = str(spec.get("name", "") or "").strip()
        if not name:
            continue
        value = output.get(name)
        field_type = str(spec.get("type", "string") or "string").strip().lower() or "string"
        nullable = bool(spec.get("nullable", False))

        if value is None:
            if nullable:
                validated[name] = None
                continue
            raise CodeExecutionError(f"output field '{name}' cannot be null")

        if field_type == "string":
            if not isinstance(value, str):
                raise CodeExecutionError(f"output field '{name}' must be string")
        elif field_type == "number":
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise CodeExecutionError(f"output field '{name}' must be number")
        elif field_type == "integer":
            if not isinstance(value, int) or isinstance(value, bool):
                raise CodeExecutionError(f"output field '{name}' must be integer")
        elif field_type == "boolean":
            if not isinstance(value, bool):
                raise CodeExecutionError(f"output field '{name}' must be boolean")
        elif field_type == "object":
            if not isinstance(value, dict):
                raise CodeExecutionError(f"output field '{name}' must be object")
        elif field_type == "array":
            if not isinstance(value, list):
                raise CodeExecutionError(f"output field '{name}' must be array")
            items_type_raw = spec.get("items_type", spec.get("itemsType"))
            items_type = str(items_type_raw or "").strip().lower()
            if items_type:
                _validate_array_item_type(name, value, items_type)
        else:
            raise CodeExecutionError(f"output field '{name}' has unsupported type: {field_type}")

        enum_values = spec.get("enum")
        if isinstance(enum_values, list) and enum_values:
            value_text = str(value)
            allowed = {str(item) for item in enum_values}
            if value_text not in allowed:
                raise CodeExecutionError(
                    f"output field '{name}' value '{value_text}' is not in enum"
                )

        validated[name] = value

    return validated


def _build_subprocess_env() -> dict[str, str]:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONUNBUFFERED": "1",
        "PYTHONIOENCODING": "utf-8",
    }
    return env


def _build_preexec_fn(memory_limit_mb: int, timeout_ms: int):
    try:
        import resource
    except Exception:  # pragma: no cover
        return None

    memory_bytes = max(16, int(memory_limit_mb)) * 1024 * 1024
    cpu_seconds = max(1, int(math.ceil(timeout_ms / 1000.0)))

    def _apply_limits() -> None:
        try:
            resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
        except Exception:
            pass
        try:
            resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 1))
        except Exception:
            pass

    return _apply_limits


def _resolve_runner(language: str) -> tuple[list[str], Path]:
    root = Path(__file__).resolve().parent
    lang = language.strip().lower()
    if lang == "python":
        runner = root / "code_executor_runners" / "python_runner.py"
        cmd = [sys.executable, "-I", str(runner)]
        return cmd, runner
    if lang == "javascript":
        runner = root / "code_executor_runners" / "js_runner.mjs"
        cmd = ["node", str(runner)]
        return cmd, runner
    raise CodeExecutionError(f"Unsupported code executor language: {language}")


def execute_code(
    *,
    language: str,
    code: str,
    entrypoint: str,
    inputs: dict[str, Any],
    output_fields: list[dict[str, Any]],
    timeout_ms: int | None = None,
) -> CodeExecutionResult:
    settings = get_settings()
    default_timeout_ms = int(
        getattr(settings, "workflow_code_executor_timeout_ms", 5000) or 5000
    )
    max_timeout_ms = int(
        getattr(settings, "workflow_code_executor_max_timeout_ms", 5000) or 5000
    )
    timeout = max(100, min(timeout_ms or default_timeout_ms, max_timeout_ms))
    memory_limit_mb = int(
        getattr(settings, "workflow_code_executor_memory_limit_mb", 128) or 128
    )
    max_output_chars = int(
        getattr(settings, "workflow_code_executor_max_output_chars", 16000) or 16000
    )

    lang = str(language or "").strip().lower()
    source = str(code or "")
    entry = str(entrypoint or "main").strip() or "main"

    if not source.strip():
        raise CodeExecutionError("Code is required")
    if not _ENTRYPOINT_RE.fullmatch(entry):
        raise CodeExecutionError("Entrypoint must match [a-zA-Z_][a-zA-Z0-9_]*")

    _validate_import_whitelist(lang, source)

    command, runner = _resolve_runner(lang)
    if not runner.exists():
        raise CodeExecutionError(f"Runner not found: {runner}")

    normalized_inputs: dict[str, Any] = {}
    for raw_key, raw_value in (inputs or {}).items():
        key = str(raw_key or "").strip()
        if not key:
            continue
        normalized_inputs[key] = raw_value

    payload = {
        "language": lang,
        "code": source,
        "entrypoint": entry,
        "inputs": normalized_inputs,
        "outputFields": output_fields,
        "timeoutMs": timeout,
        "maxOutputChars": max_output_chars,
        "allowedModules": sorted(
            get_python_allowed_modules() if lang == "python" else get_javascript_allowed_modules()
        ),
    }

    preexec_fn = _build_preexec_fn(memory_limit_mb=memory_limit_mb, timeout_ms=timeout)

    try:
        proc = subprocess.run(
            command,
            input=json.dumps(payload, ensure_ascii=False),
            text=True,
            capture_output=True,
            timeout=timeout / 1000.0,
            env=_build_subprocess_env(),
            preexec_fn=preexec_fn,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise CodeExecutionError(
            f"Code execution timed out after {timeout}ms"
        ) from exc
    except Exception as exc:
        raise CodeExecutionError(f"Failed to launch code executor process: {exc}") from exc

    stdout_text = (proc.stdout or "").strip()
    stderr_text = (proc.stderr or "").strip()

    if proc.returncode != 0:
        err = stderr_text or stdout_text or f"runner exited with code {proc.returncode}"
        raise CodeExecutionError(f"Code execution failed: {err}")

    try:
        result = json.loads(stdout_text or "{}")
    except Exception as exc:
        raise CodeExecutionError(
            f"Code execution produced invalid JSON result: {stdout_text[:400]}"
        ) from exc

    if not isinstance(result, dict):
        raise CodeExecutionError("Code execution result payload is invalid")

    ok = bool(result.get("ok", False))
    if not ok:
        message = str(result.get("error") or "Unknown code execution error")
        raise CodeExecutionError(message)

    raw_output = result.get("output")
    if not isinstance(raw_output, dict):
        raise CodeExecutionError(
            f"Code executor '{entry}' must return an object"
        )

    validated_output = _validate_output_against_schema(raw_output, output_fields)

    if _json_size(validated_output) > max_output_chars:
        raise CodeExecutionError(
            f"Code executor output exceeds max size {max_output_chars} chars"
        )

    stdout_log = str(result.get("stdout") or "")
    stderr_log = str(result.get("stderr") or "")
    if len(stdout_log) > max_output_chars:
        stdout_log = stdout_log[:max_output_chars] + "...(truncated)"
    if len(stderr_log) > max_output_chars:
        stderr_log = stderr_log[:max_output_chars] + "...(truncated)"

    return CodeExecutionResult(
        output=validated_output,
        stdout=stdout_log,
        stderr=stderr_log,
    )
