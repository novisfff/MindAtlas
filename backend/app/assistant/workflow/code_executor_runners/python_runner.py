from __future__ import annotations

import io
import json
import traceback
from typing import Any


def _read_payload() -> dict[str, Any]:
    raw = __import__("sys").stdin.read()
    if not raw.strip():
        return {}
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("payload must be object")
    return payload


def _build_safe_builtins(
    allowed_modules: set[str],
    stdout_capture: io.StringIO,
) -> dict[str, Any]:
    import builtins as _b

    allowed_builtin_names = {
        "abs",
        "all",
        "any",
        "bool",
        "dict",
        "enumerate",
        "Exception",
        "filter",
        "float",
        "int",
        "isinstance",
        "issubclass",
        "len",
        "list",
        "map",
        "max",
        "min",
        "object",
        "pow",
        "print",
        "property",
        "range",
        "repr",
        "reversed",
        "round",
        "set",
        "slice",
        "sorted",
        "str",
        "sum",
        "tuple",
        "type",
        "ValueError",
        "TypeError",
        "RuntimeError",
        "zip",
        "__build_class__",
        "getattr",
        "setattr",
        "hasattr",
    }

    safe_builtins = {name: getattr(_b, name) for name in allowed_builtin_names if hasattr(_b, name)}

    def _safe_import(name: str, globals=None, locals=None, fromlist=(), level=0):
        root = str(name or "").split(".", 1)[0]
        if root not in allowed_modules:
            raise ImportError(f"Import not allowed: {name}")
        return _b.__import__(name, globals, locals, fromlist, level)

    def _safe_print(*args, **kwargs):
        text = " ".join(str(arg) for arg in args)
        if kwargs.get("end") is not None:
            text += str(kwargs.get("end"))
        else:
            text += "\n"
        stdout_capture.write(text)

    safe_builtins["__import__"] = _safe_import
    safe_builtins["print"] = _safe_print
    return safe_builtins


def _emit(payload: dict[str, Any]) -> None:
    __import__("sys").stdout.write(json.dumps(payload, ensure_ascii=False, default=str))


def _main() -> None:
    stdout_capture = io.StringIO()
    try:
        payload = _read_payload()
        code = str(payload.get("code") or "")
        entrypoint = str(payload.get("entrypoint") or "main").strip() or "main"
        inputs_raw = payload.get("inputs")
        if inputs_raw is None:
            inputs_raw = {}
        if not isinstance(inputs_raw, dict):
            raise ValueError("inputs must be object")
        inputs = {
            str(key): value
            for key, value in inputs_raw.items()
            if isinstance(key, str) and key
        }
        allowed_modules = set(payload.get("allowedModules") or [])
        max_output_chars = int(payload.get("maxOutputChars") or 16000)

        safe_builtins = _build_safe_builtins(allowed_modules, stdout_capture)
        scope: dict[str, Any] = {
            "__builtins__": safe_builtins,
            "__name__": "__code_executor__",
            "__package__": None,
        }

        exec(code, scope, scope)

        entry = scope.get(entrypoint)
        if not callable(entry):
            raise RuntimeError(f"Entrypoint '{entrypoint}' is not defined or not callable")

        output = entry(**inputs)
        if not isinstance(output, dict):
            raise RuntimeError(f"{entrypoint}(...) must return an object")

        stdout_log = stdout_capture.getvalue()
        if len(stdout_log) > max_output_chars:
            stdout_log = stdout_log[:max_output_chars] + "...(truncated)"

        _emit({
            "ok": True,
            "output": output,
            "stdout": stdout_log,
            "stderr": "",
        })
    except Exception as exc:
        stdout_log = stdout_capture.getvalue()
        err = f"{exc}\n{traceback.format_exc(limit=8)}"
        _emit(
            {
                "ok": False,
                "error": str(exc),
                "stdout": stdout_log,
                "stderr": err,
            }
        )


if __name__ == "__main__":
    _main()
