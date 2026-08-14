"""Strictly scoped module replacement for tests of genuinely optional imports.

Installed framework/runtime packages are deliberately rejected.  A test that
needs a replacement must restore every process-global dimension before the
scope returns, including application modules imported while the replacement
was active.
"""

from __future__ import annotations

from contextlib import contextmanager
import importlib
import os
from pathlib import Path
import re
import sys
import threading
from types import ModuleType
from typing import Iterator, Mapping


class ScopedModulesError(RuntimeError):
    """Raised when a scoped replacement would violate the test boundary."""


class _Missing:
    pass


_MISSING = _Missing()
_SCOPE_LOCK = threading.RLock()
_ACTIVE_KEYS: set[str] = set()
_PIN_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9_.-]*)==")


def _normalise_root(name: str) -> str:
    return name.split(".", 1)[0].replace("-", "_").lower()


def _lock_owned_roots() -> set[str]:
    roots = {
        "fastapi",
        "starlette",
        "sqlalchemy",
        "pydantic",
        "cryptography",
    }
    requirements_dir = Path(__file__).resolve().parents[1] / "requirements"
    for lock in requirements_dir.glob("*.lock"):
        try:
            lines = lock.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            match = _PIN_RE.match(line)
            if match:
                roots.add(_normalise_root(match.group(1)))
    return roots


_LOCK_OWNED_ROOTS = _lock_owned_roots()


def _snapshot_app_modules(roots: tuple[str, ...]) -> dict[str, ModuleType]:
    return {
        name: module
        for name, module in sys.modules.items()
        if isinstance(module, ModuleType)
        and any(name == root or name.startswith(f"{root}.") for root in roots)
    }


def _restore_modules(snapshot: Mapping[str, ModuleType | _Missing]) -> None:
    for name, module in snapshot.items():
        if module is _MISSING:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


def _remove_new_app_modules(
    roots: tuple[str, ...],
    preexisting: Mapping[str, ModuleType],
) -> None:
    for name in list(sys.modules):
        if not any(name == root or name.startswith(f"{root}.") for root in roots):
            continue
        if name not in preexisting:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = preexisting[name]


def _reset_registered_test_caches() -> None:
    """Delegate to the repository's canonical cache reset hook when present."""

    try:
        from tests._bootstrap import reset_caches

        reset_caches()
    except Exception:
        # This helper is also importable during collection, before the app
        # bootstrap path exists.  Module restoration remains authoritative.
        pass


@contextmanager
def scoped_modules(
    replacements: Mapping[str, ModuleType],
    *,
    app_module_roots: tuple[str, ...],
) -> Iterator[None]:
    """Temporarily install optional modules and restore process state exactly.

    Lock-owned package roots are rejected so this helper cannot become a
    backdoor for replacing the real FastAPI/Starlette/runtime environment.
    Scopes are serialized across threads and overlapping nested keys fail
    explicitly; non-overlapping nested scopes restore in LIFO order.
    """

    keys = set(replacements)
    if not keys:
        raise ScopedModulesError("scoped module replacement cannot be empty")
    invalid = sorted(
        name for name in keys if _normalise_root(name) in _LOCK_OWNED_ROOTS
    )
    if invalid:
        raise ScopedModulesError(
            "lock-owned modules cannot be replaced: " + ", ".join(invalid)
        )
    for name, module in replacements.items():
        if not isinstance(name, str) or not name or not isinstance(module, ModuleType):
            raise ScopedModulesError("replacements must map module names to ModuleType objects")
        if name != name.strip():
            raise ScopedModulesError(f"invalid module name: {name!r}")

    if not _SCOPE_LOCK.acquire(blocking=False):
        raise ScopedModulesError("scoped module replacements cannot run in parallel")
    if _ACTIVE_KEYS.intersection(keys):
        _SCOPE_LOCK.release()
        overlap = sorted(_ACTIVE_KEYS.intersection(keys))
        raise ScopedModulesError("overlapping scoped module keys: " + ", ".join(overlap))

    module_snapshot: dict[str, ModuleType | _Missing] = {
        name: sys.modules.get(name, _MISSING) for name in keys
    }
    environment_snapshot = dict(os.environ)
    preexisting_app_modules = _snapshot_app_modules(app_module_roots)
    _ACTIVE_KEYS.update(keys)
    try:
        sys.modules.update(replacements)
        importlib.invalidate_caches()
        yield
    finally:
        try:
            _restore_modules(module_snapshot)
            _remove_new_app_modules(app_module_roots, preexisting_app_modules)
            os.environ.clear()
            os.environ.update(environment_snapshot)
            _reset_registered_test_caches()
            _restore_modules(module_snapshot)
            _remove_new_app_modules(app_module_roots, preexisting_app_modules)
            importlib.invalidate_caches()
        finally:
            _ACTIVE_KEYS.difference_update(keys)
            _SCOPE_LOCK.release()


__all__ = ("ScopedModulesError", "scoped_modules")
