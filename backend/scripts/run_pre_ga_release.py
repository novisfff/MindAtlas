#!/usr/bin/env python3
"""Closed host-runner command surface for the pre-GA release gate.

This module owns validation and orchestration boundaries only.  It never
accepts an outcome/pass value, scenario subset, database URL, or raw secret
from a caller, and it never emits a successful qualification without a real
profile producing signed evidence.
"""

from __future__ import annotations

import argparse
import hashlib
from http.cookiejar import CookieJar
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPCookieProcessor, Request, build_opener
from uuid import uuid4


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

REQUIRED_SERVICES = {
    "postgres",
    "minio",
    "minio-init",
    "schema-migrate",
    "scripted-provider",
    "api",
    "assistant-worker-a",
    "assistant-worker-b",
    "web",
}
RELEASE_SECRET_FILES = (
    "postgres-password",
    "minio-password",
    "setup-token",
    "operator-password",
    "session-mac-ring",
    "csrf-session-material",
    "fernet-key",
    "idempotency-secret",
    "interrupt-pepper",
    "reconciliation-hmac",
)
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_FORBIDDEN_CAPTURE_KEY = re.compile(
    r"(?i)(password|passwd|secret|token|api[_-]?key|authorization|bearer|cookie|prompt|"
    r"entry[_-]?body|artifact[_-]?body|provider[_-]?credential)"
)
_MAX_TARGET_HTTP_BODY = 8 * 1024 * 1024
_SESSION_COOKIE_NAME = "mindatlas_session"
_CSRF_COOKIE_NAME = "mindatlas_csrf"
_CSRF_HEADER_NAME = "X-MindAtlas-CSRF"


class ReleaseCliError(ValueError):
    def __init__(self, safe_code: str) -> None:
        self.safe_code = safe_code
        super().__init__(safe_code)


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _json_file(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        raise ReleaseCliError("release_json_input_invalid") from None


def _require_fd(fd: int, *, code: str = "release_secret_fd_unavailable") -> None:
    if fd < 0:
        raise ReleaseCliError(code)
    try:
        os.fstat(fd)
    except OSError:
        raise ReleaseCliError(code) from None


def _require_file(path: Path | None, *, code: str) -> Path:
    if not isinstance(path, Path) or not path.is_file():
        raise ReleaseCliError(code)
    return path


def _require_safe_alias(value: str | None) -> str:
    if not isinstance(value, str) or _SAFE_ID.fullmatch(value) is None:
        raise ReleaseCliError("release_target_alias_invalid")
    return value


def _write_secret(path: Path, size: int = 48) -> None:
    path.write_bytes(secrets.token_bytes(size))
    path.chmod(0o600)


def _file_digest(path: Path) -> str:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        raise ReleaseCliError("release_file_unreadable") from None


def _read_fd_secret(fd: int, *, code: str, max_bytes: int = 4096) -> str:
    """Read a bounded UTF-8 secret from an already-open descriptor.

    Descriptors are used instead of command-line values so process listings and
    shell history cannot expose the Operator password. A single terminal line
    ending is accepted because protected runners commonly materialize secrets
    with a newline; all other bytes remain part of the secret.
    """

    _require_fd(fd, code=code)
    duplicate = os.dup(fd)
    try:
        raw = bytearray()
        while True:
            chunk = os.read(duplicate, min(1024, max_bytes + 1 - len(raw)))
            raw.extend(chunk)
            if not chunk:
                break
            if len(raw) > max_bytes:
                raise ReleaseCliError("release_secret_too_large")
    except OSError:
        raise ReleaseCliError(code) from None
    finally:
        os.close(duplicate)
    if raw.endswith(b"\n"):
        raw.pop()
        if raw.endswith(b"\r"):
            raw.pop()
    try:
        value = bytes(raw).decode("utf-8")
    except UnicodeDecodeError:
        raise ReleaseCliError("release_secret_encoding_invalid") from None
    finally:
        for index in range(len(raw)):
            raw[index] = 0
    if not value:
        raise ReleaseCliError("release_secret_empty")
    return value


def _sanitize_provisioning_configuration(value: Any) -> Any:
    """Drop content-bearing or credential-bearing capture fields recursively."""

    if isinstance(value, dict):
        return {
            key: _sanitize_provisioning_configuration(item)
            for key, item in value.items()
            if isinstance(key, str) and _FORBIDDEN_CAPTURE_KEY.search(key) is None
        }
    if isinstance(value, list):
        return [_sanitize_provisioning_configuration(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise ReleaseCliError("release_target_provisioning_bundle_invalid")


def _validate_auth_set_cookie_headers(headers: tuple[str, ...] | list[str]) -> None:
    """Require the production Session/CSRF cookie contract on login."""

    by_name: dict[str, str] = {}
    for header in headers:
        if not isinstance(header, str):
            continue
        name = header.split("=", 1)[0].strip().lower()
        if name in {_SESSION_COOKIE_NAME, _CSRF_COOKIE_NAME}:
            by_name[name] = header
    if set(by_name) != {_SESSION_COOKIE_NAME, _CSRF_COOKIE_NAME}:
        raise ReleaseCliError("release_target_cookie_contract_invalid")
    for name, header in by_name.items():
        attributes = header.lower()
        if "secure" not in {part.strip() for part in attributes.split(";")}:
            raise ReleaseCliError("release_target_cookie_contract_invalid")
        if "samesite=lax" not in attributes and "samesite=strict" not in attributes and "samesite=none" not in attributes:
            raise ReleaseCliError("release_target_cookie_contract_invalid")
        if name == _SESSION_COOKIE_NAME and "httponly" not in {
            part.strip() for part in attributes.split(";")
        }:
            raise ReleaseCliError("release_target_cookie_contract_invalid")


class _TargetHttpClient:
    """Minimal authenticated HTTP client for target capture.

    The client keeps cookies in memory and returns only decoded API ``data``;
    response bodies, headers, and cookie values never reach the caller's logs or
    the provisioning bundle.
    """

    def __init__(self, base_url: str, *, opener: Any | None = None) -> None:
        parsed = urlparse(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ReleaseCliError("release_target_url_invalid")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ReleaseCliError("release_target_url_invalid")
        self.base_url = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
        self.origin = self.base_url
        self._jar = CookieJar()
        self._opener = opener or build_opener(HTTPCookieProcessor(self._jar))

    def _cookie(self, name: str) -> str | None:
        for cookie in self._jar:
            if cookie.name == name:
                return cookie.value
        return None

    @staticmethod
    def _set_cookie_headers(response: Any) -> tuple[str, ...]:
        headers = getattr(response, "headers", None)
        if headers is None:
            return ()
        values = headers.get_all("Set-Cookie", [])
        if isinstance(values, str):
            return (values,)
        return tuple(value for value in values if isinstance(value, str))

    def request(self, path: str, *, method: str = "GET", body: dict[str, Any] | None = None, csrf: bool = False) -> Any:
        if not path.startswith("/") or ".." in path:
            raise ReleaseCliError("release_target_path_invalid")
        encoded = None
        headers = {"Accept": "application/json", "Origin": self.origin}
        if body is not None:
            encoded = _canonical(body)
            headers["Content-Type"] = "application/json"
        if csrf:
            csrf_value = self._cookie(_CSRF_COOKIE_NAME)
            if not csrf_value:
                raise ReleaseCliError("release_target_csrf_cookie_missing")
            headers[_CSRF_HEADER_NAME] = csrf_value
        request = Request(
            self.base_url + path,
            data=encoded,
            headers=headers,
            method=method,
        )
        try:
            response = self._opener.open(request, timeout=20)
            status_value = getattr(response, "status", None)
            status = int(status_value if status_value is not None else response.getcode())
            if status < 200 or status >= 300:
                raise ReleaseCliError("release_target_http_request_failed")
            raw = response.read(_MAX_TARGET_HTTP_BODY + 1)
        except ReleaseCliError:
            raise
        except (HTTPError, URLError, OSError, TimeoutError):
            raise ReleaseCliError("release_target_authenticated_capture_unavailable") from None
        if len(raw) > _MAX_TARGET_HTTP_BODY:
            raise ReleaseCliError("release_target_response_too_large")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise ReleaseCliError("release_target_response_invalid") from None
        if not isinstance(payload, dict) or payload.get("success") is not True or "data" not in payload:
            raise ReleaseCliError("release_target_http_request_failed")
        return payload["data"], response

    def login(self, password: str) -> None:
        _, response = self.request(
            "/api/operator-auth/login",
            method="POST",
            body={"password": password},
        )
        _validate_auth_set_cookie_headers(self._set_cookie_headers(response))
        if self._cookie(_SESSION_COOKIE_NAME) is None or self._cookie(_CSRF_COOKIE_NAME) is None:
            raise ReleaseCliError("release_target_cookie_contract_invalid")
        session, _ = self.request("/api/operator-auth/session")
        if not isinstance(session, dict) or session.get("authenticated") is not True:
            raise ReleaseCliError("release_target_operator_auth_invalid")

    def get(self, path: str) -> Any:
        data, _ = self.request(path)
        return data


def _validate_target_material(target_path: Path, provisioning_path: Path) -> dict[str, str]:
    """Validate target/configuration bytes before creating any run state."""
    target_result = verify_target(target_path)
    raw_provisioning = _json_file(provisioning_path)
    if not isinstance(raw_provisioning, dict):
        raise ReleaseCliError("release_target_provisioning_bundle_invalid")
    try:
        from app.release.target_fixture import (
            RehearsalInitializationFixtureV1,
            configuration_digest,
        )

        if {"target", "provisioning", "provisioningDigest", "fixtureDigest"} <= set(raw_provisioning):
            fixture = RehearsalInitializationFixtureV1.model_validate(raw_provisioning)
            if fixture.target.qualification_target_digest != target_result["targetDigest"]:
                raise ReleaseCliError("release_target_fixture_target_mismatch")
            fixture_digest = fixture.fixture_digest
        else:
            # A plain provisioning bundle is allowed for target capture, but it
            # is still a typed, JSON-only, sensitive-field-scanned object.
            fixture_digest = configuration_digest(raw_provisioning)
    except ReleaseCliError:
        raise
    except (TypeError, ValueError):
        raise ReleaseCliError("release_target_provisioning_bundle_invalid") from None
    return {
        "targetDigest": target_result["targetDigest"],
        "fixtureDigest": fixture_digest,
        "targetFileDigest": _file_digest(target_path),
        "provisioningBundleDigest": _file_digest(provisioning_path),
    }


def _validate_evidence_run(args: argparse.Namespace) -> None:
    parsed = urlparse(args.profile_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ReleaseCliError("release_profile_url_invalid")
    if args.output_dir.exists():
        if not args.output_dir.is_dir() or any(args.output_dir.iterdir()):
            raise ReleaseCliError("release_output_dir_must_be_empty")
    else:
        args.output_dir.mkdir(parents=True)
        args.output_dir.chmod(0o700)
    _require_fd(args.signing_key_fd, code="release_signing_key_fd_unavailable")
    if not args.trust_set.is_file():
        raise ReleaseCliError("release_trust_set_missing")


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml

        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (ImportError, OSError, UnicodeError, ValueError):
        raise ReleaseCliError("release_compose_invalid") from None
    if not isinstance(raw, dict):
        raise ReleaseCliError("release_compose_invalid")
    return raw


def validate_compose(compose_path: Path, lock_path: Path) -> dict[str, Any]:
    from scripts.lock_release_images import load_lock

    try:
        lock = load_lock(lock_path)
    except ValueError as exc:
        raise ReleaseCliError(str(exc)) from None
    compose = _load_yaml(compose_path)
    services = compose.get("services")
    if not isinstance(services, dict) or set(services) != REQUIRED_SERVICES:
        raise ReleaseCliError("release_compose_service_inventory_invalid")
    if any(key in compose for key in ("extends", "include")):
        raise ReleaseCliError("release_compose_must_be_standalone")
    networks = compose.get("networks")
    internal = networks.get("release-internal", {}).get("internal") if isinstance(networks, dict) else None
    if internal is not True:
        raise ReleaseCliError("release_compose_network_not_internal")
    worker_a = services["assistant-worker-a"]
    worker_b = services["assistant-worker-b"]
    if worker_a.get("hostname") == worker_b.get("hostname"):
        raise ReleaseCliError("release_compose_workers_not_distinct")
    for name, service in services.items():
        encoded = json.dumps(service, ensure_ascii=False).lower()
        if "latest" in encoded or "neo4j" in encoded:
            raise ReleaseCliError("release_compose_mutable_or_unapproved_dependency")
        if ":-" in encoded:
            raise ReleaseCliError("release_compose_default_substitution_forbidden")
        if service.get("network_mode") in {"host", "none"} or service.get("privileged") is True:
            raise ReleaseCliError("release_compose_host_or_privileged_mode_forbidden")
        if name not in {"postgres", "minio", "minio-init"} and isinstance(service.get("build"), (dict, str)):
            raise ReleaseCliError("release_compose_application_build_must_be_externalized")
        for port in service.get("ports", ()) or ():
            rendered = str(port)
            if not rendered.startswith("127.0.0.1:"):
                raise ReleaseCliError("release_compose_host_port_must_be_loopback")
        environment = service.get("environment", {})
        if isinstance(environment, dict):
            for key, value in environment.items():
                rendered = str(value or "").lower()
                if key in {"DATABASE_URL", "MINIO_ENDPOINT", "AI_PROVIDER_BASE_URL"} and any(
                    marker in rendered for marker in ("host.docker.internal", "127.0.0.1", "localhost")
                ):
                    raise ReleaseCliError("release_compose_host_dependency_forbidden")
                if key == "AI_PROVIDER_BASE_URL" and name != "scripted-provider" and "scripted-provider" not in rendered:
                    raise ReleaseCliError("release_compose_paid_provider_forbidden")
    expected_images = {item["role"]: item["reference"] for item in lock["images"]}
    for service_name, role in (("postgres", "postgres"), ("minio", "minio"), ("minio-init", "minio-client")):
        if services[service_name].get("image") != expected_images[role]:
            raise ReleaseCliError("release_compose_infrastructure_image_not_locked")
    backend_placeholder = "${RELEASE_BACKEND_IMAGE:?RELEASE_BACKEND_IMAGE must be an immutable backend image}"
    scripted_provider_placeholder = "${RELEASE_SCRIPTED_PROVIDER_IMAGE:?RELEASE_SCRIPTED_PROVIDER_IMAGE must be an immutable scripted-provider image}"
    web_placeholder = "${RELEASE_WEB_IMAGE:?RELEASE_WEB_IMAGE must be an immutable web image}"
    for service_name in ("schema-migrate", "api", "assistant-worker-a", "assistant-worker-b"):
        if services[service_name].get("image") != backend_placeholder:
            raise ReleaseCliError("release_compose_application_image_contract_invalid")
    if services["scripted-provider"].get("image") != scripted_provider_placeholder:
        raise ReleaseCliError("release_compose_application_image_contract_invalid")
    if services["web"].get("image") != web_placeholder:
        raise ReleaseCliError("release_compose_application_image_contract_invalid")
    volumes = compose.get("volumes", {})
    if not isinstance(volumes, dict) or len(volumes) < 6:
        raise ReleaseCliError("release_compose_volume_isolation_invalid")
    volume_names = []
    for volume in volumes.values():
        if not isinstance(volume, dict) or not isinstance(volume.get("name"), str):
            raise ReleaseCliError("release_compose_volume_name_invalid")
        name = volume["name"]
        if "${RELEASE_RUN_ID" not in name:
            raise ReleaseCliError("release_compose_volume_run_isolation_missing")
        volume_names.append(name)
    if len(volume_names) != len(set(volume_names)):
        raise ReleaseCliError("release_compose_volume_name_collision")
    for service_name in REQUIRED_SERVICES:
        if service_name in {"postgres", "minio", "assistant-worker-a", "assistant-worker-b"} and not services[service_name].get("networks"):
            raise ReleaseCliError("release_compose_service_network_missing")
    docker = shutil.which("docker")
    if docker is None:
        raise ReleaseCliError("release_compose_docker_unavailable")
    # Compose interpolation is part of the topology contract. Use disposable
    # descriptor/config paths so validation cannot read or print a caller's
    # credentials, and never forward Compose stderr to the CLI output.
    with tempfile.TemporaryDirectory(prefix="mindatlas-release-validate-") as raw_tmp:
        temp_dir = Path(raw_tmp)
        runtime_env = temp_dir / "runtime.env"
        runtime_env.write_text("APP_ENV=test\n", encoding="utf-8")
        for name in (
            "postgres-password",
            "minio-password",
            "deployment-identity",
            "trust-set",
            "rehearsal-authorization",
        ):
            (temp_dir / name).write_bytes(b"release-validation-placeholder")
        environment = dict(os.environ)
        environment.update(
            {
                "RELEASE_COMPOSE_PROJECT": "mindatlas-release-validate",
                "RELEASE_RUN_ID": "mindatlas-release-validate",
                "RELEASE_BUILD_REVISION": "0" * 40,
                "RELEASE_BACKEND_IMAGE": "registry.invalid/mindatlas/backend@sha256:" + "a" * 64,
                "RELEASE_SCRIPTED_PROVIDER_IMAGE": "registry.invalid/mindatlas/scripted-provider@sha256:" + "c" * 64,
                "RELEASE_WEB_IMAGE": "registry.invalid/mindatlas/web@sha256:" + "b" * 64,
                "RELEASE_API_PORT": "18000",
                "RELEASE_WEB_PORT": "18001",
                "RELEASE_RUNTIME_ENV_FILE": str(runtime_env),
                "RELEASE_DATABASE_URL": "postgresql://release:release@postgres:5432/release",
                "RELEASE_POSTGRES_USER": "release",
                "RELEASE_POSTGRES_DB": "release",
                "RELEASE_POSTGRES_PASSWORD_FILE": str(temp_dir / "postgres-password"),
                "RELEASE_MINIO_ACCESS_KEY": "release",
                "RELEASE_MINIO_PASSWORD_FILE": str(temp_dir / "minio-password"),
                "RELEASE_DEPLOYMENT_IDENTITY_FILE": str(temp_dir / "deployment-identity"),
                "RELEASE_TRUST_SET_FILE": str(temp_dir / "trust-set"),
                "RELEASE_REHEARSAL_AUTHORIZATION_FILE": str(temp_dir / "rehearsal-authorization"),
                "RELEASE_SCRIPTED_PROVIDER_SCRIPT": str(temp_dir / "scripted-provider.json"),
            }
        )
        (temp_dir / "scripted-provider.json").write_text(
            '{"schemaVersion":1,"scenarioId":"release","steps":[{"scenarioId":"release","requestOrdinal":1,"expectedToolNames":[],"responseKind":"content","toolName":null,"faultCode":null}]}',
            encoding="utf-8",
        )
        result = subprocess.run(
            [docker, "compose", "-f", str(compose_path), "config", "--quiet"],
            cwd=str(compose_path.parent),
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    if result.returncode != 0:
        raise ReleaseCliError("release_compose_interpolation_invalid")
    return {
        "services": sorted(services),
        "workers": [worker_a["hostname"], worker_b["hostname"]],
        "networkInternal": True,
        "releaseImagesLockDigest": lock["lockDigest"],
    }


def prepare_profile(args: argparse.Namespace) -> dict[str, Any]:
    if args.kind not in {"automated_qualification", "production_rehearsal"}:
        raise ReleaseCliError("release_evidence_kind_invalid")
    if args.run_dir.exists() or args.run_dir.is_symlink():
        raise ReleaseCliError("release_run_dir_must_not_exist")
    _require_file(args.qualification_target, code="release_qualification_target_missing")
    _require_file(args.target_provisioning_bundle, code="release_target_provisioning_bundle_missing")
    material = _validate_target_material(args.qualification_target, args.target_provisioning_bundle)
    _require_fd(args.signing_key_fd, code="release_signing_key_fd_unavailable")
    _require_file(args.trust_set, code="release_trust_set_missing")
    oci_bundle = getattr(args, "oci_bundle", None)
    if oci_bundle is None:
        configured_bundle = os.environ.get("MINDATLAS_RELEASE_OCI_BUNDLE", "")
        oci_bundle = Path(configured_bundle) if configured_bundle else None
    _require_file(oci_bundle, code="release_oci_bundle_missing")
    if oci_bundle.is_symlink():
        raise ReleaseCliError("release_oci_bundle_must_be_regular_file")
    try:
        from app.release.trust import load_trust_set

        load_trust_set(args.trust_set)
    except Exception:
        raise ReleaseCliError("release_trust_set_invalid") from None
    if not bool(getattr(args, "no_build", False)):
        raise ReleaseCliError("release_profile_requires_no_build")
    automation_manifest_digest: str | None = None
    automation_attestation_digest: str | None = None
    if args.kind == "production_rehearsal":
        _require_file(getattr(args, "automation_evidence", None), code="release_automation_evidence_missing")
        try:
            automation_manifest, automation_attestation = _verified_manifest(
                args.automation_evidence,
                args.trust_set,
            )
        except ReleaseCliError:
            raise ReleaseCliError("release_automation_evidence_invalid") from None
        if automation_manifest.evidence_kind != "automated_qualification":
            raise ReleaseCliError("release_automation_evidence_kind_invalid")
        if automation_manifest.qualification_target_digest != material["targetDigest"]:
            raise ReleaseCliError("release_automation_evidence_target_mismatch")
        from app.release.trust import attestation_object_digest

        automation_manifest_digest = automation_manifest.manifest_digest
        automation_attestation_digest = attestation_object_digest(automation_attestation)
        _require_safe_alias(getattr(args, "attempt_ledger_alias", None))
        _require_fd(
            int(getattr(args, "attempt_ledger_credential_fd", -1)),
            code="release_attempt_ledger_credential_fd_unavailable",
        )
    try:
        from app.release.scenarios import REQUIRED_ASSERTION_SET_DIGEST, SCENARIO_SET_DIGEST
        from scripts.lock_release_images import load_lock

        release_images_lock_digest = load_lock(
            BACKEND_ROOT.parent / "deploy" / "release-images.lock"
        )["lockDigest"]
    except Exception:
        raise ReleaseCliError("release_profile_contract_material_invalid") from None
    args.run_dir.mkdir(parents=True, mode=0o700)
    secrets_dir = args.run_dir / "secrets"
    secrets_dir.mkdir(mode=0o700)
    for name in RELEASE_SECRET_FILES:
        _write_secret(secrets_dir / name)
    # Target material may include non-secret configuration text, so retain it
    # only under the private run directory and never echo or upload it.
    shutil.copyfile(args.qualification_target, args.run_dir / "qualification-target.json")
    shutil.copyfile(args.target_provisioning_bundle, args.run_dir / "target-provisioning.bundle")
    shutil.copyfile(oci_bundle, args.run_dir / "release.oci.tar")
    shutil.copyfile(args.trust_set, args.run_dir / "trust-set.json")
    for name in ("qualification-target.json", "target-provisioning.bundle", "release.oci.tar"):
        (args.run_dir / name).chmod(0o600)
    (args.run_dir / "trust-set.json").chmod(0o644)
    state = {
        "schemaVersion": 1,
        "kind": args.kind,
        "runId": str(uuid4()),
        "state": "prepared",
        "secretDirectory": "secrets",
        "targetFile": "qualification-target.json",
        "provisioningBundle": "target-provisioning.bundle",
        "trustSet": "trust-set.json",
        "noBuild": True,
        "qualificationTargetDigest": material["targetDigest"],
        "initializationFixtureDigest": material["fixtureDigest"],
        "targetFileDigest": material["targetFileDigest"],
        "provisioningBundleDigest": material["provisioningBundleDigest"],
        "scenarioSetDigest": SCENARIO_SET_DIGEST,
        "requiredAssertionSetDigest": REQUIRED_ASSERTION_SET_DIGEST,
        "releaseImagesLockDigest": release_images_lock_digest,
        "trustSetDigest": _file_digest(args.trust_set),
        "ociBundle": "release.oci.tar",
        "ociBundleDigest": _file_digest(oci_bundle),
    }
    if args.kind == "production_rehearsal":
        shutil.copyfile(args.automation_evidence, args.run_dir / "automation-evidence.json")
        (args.run_dir / "automation-evidence.json").chmod(0o600)
        state["automationEvidence"] = "automation-evidence.json"
        state["ociBundle"] = "release.oci.tar"
        state["automationEvidenceDigest"] = _file_digest(args.automation_evidence)
        state["attemptLedgerAlias"] = _require_safe_alias(args.attempt_ledger_alias)
        state["automationManifestDigest"] = automation_manifest_digest
        state["automationAttestationDigest"] = automation_attestation_digest
    (args.run_dir / "profile-state.json").write_bytes(_canonical(state) + b"\n")
    (args.run_dir / "profile-state.json").chmod(0o600)
    return {"kind": args.kind, "state": state["state"], "runId": state["runId"]}


def _require_prepared_run(run_dir: Path) -> dict[str, Any]:
    state_path = run_dir / "profile-state.json"
    if not run_dir.is_dir() or not state_path.is_file():
        raise ReleaseCliError("release_run_not_prepared")
    state = _json_file(state_path)
    if not isinstance(state, dict) or state.get("schemaVersion") != 1:
        raise ReleaseCliError("release_run_state_invalid")
    return state


def run_profile(args: argparse.Namespace) -> dict[str, Any]:
    state = _require_prepared_run(args.run_dir)
    if not bool(getattr(args, "no_build", False)):
        raise ReleaseCliError("release_profile_requires_no_build")
    if str(getattr(args, "kind", "") or state.get("kind")) != state.get("kind"):
        raise ReleaseCliError("release_run_kind_mismatch")
    try:
        material = _validate_target_material(
            args.run_dir / str(state.get("targetFile", "qualification-target.json")),
            args.run_dir / str(state.get("provisioningBundle", "target-provisioning.bundle")),
        )
        trust_path = args.run_dir / str(state.get("trustSet", "trust-set.json"))
        trust_digest = _file_digest(trust_path)
    except ReleaseCliError:
        raise ReleaseCliError("release_target_material_drift") from None
    if (
        material["targetDigest"] != state.get("qualificationTargetDigest")
        or material["fixtureDigest"] != state.get("initializationFixtureDigest")
        or material["targetFileDigest"] != state.get("targetFileDigest")
        or material["provisioningBundleDigest"] != state.get("provisioningBundleDigest")
        or trust_digest != state.get("trustSetDigest")
    ):
        raise ReleaseCliError("release_target_material_drift")
    supplied_oci = getattr(args, "oci_bundle", None)
    if supplied_oci is None:
        configured_bundle = os.environ.get("MINDATLAS_RELEASE_OCI_BUNDLE", "")
        supplied_oci = Path(configured_bundle) if configured_bundle else None
    if supplied_oci is None:
        supplied_oci = args.run_dir / str(state.get("ociBundle", "release.oci.tar"))
    _require_file(supplied_oci, code="release_oci_bundle_missing")
    if supplied_oci.is_symlink():
        raise ReleaseCliError("release_oci_bundle_must_be_regular_file")
    if _file_digest(supplied_oci) != state.get("ociBundleDigest"):
        raise ReleaseCliError("release_oci_bundle_drift")
    if state.get("kind") == "production_rehearsal":
        evidence_path = getattr(args, "automation_evidence", None)
        if evidence_path is None:
            evidence_path = args.run_dir / str(state.get("automationEvidence", "automation-evidence.json"))
        else:
            _require_file(evidence_path, code="release_automation_evidence_missing")
        _require_file(evidence_path, code="release_automation_evidence_missing")
        if _file_digest(evidence_path) != state.get("automationEvidenceDigest"):
            raise ReleaseCliError("release_automation_evidence_drift")
    # Starting a profile is intentionally a hard boundary: the real runner is
    # an explicitly installed protected executable. It owns Compose, service
    # health, scenario execution, teardown, and evidence finalization. The host
    # CLI never accepts a caller-supplied outcome or treats its exit status alone
    # as evidence; it verifies the produced signed object below.
    if not shutil.which("docker"):
        raise ReleaseCliError("release_profile_docker_unavailable")
    runner_value = os.environ.get("MINDATLAS_RELEASE_PROTECTED_RUNNER", "")
    runner = Path(runner_value) if runner_value else None
    if (
        runner is None
        or not runner.is_absolute()
        or runner.is_symlink()
        or not runner.is_file()
        or not os.access(runner, os.X_OK)
    ):
        raise ReleaseCliError("release_profile_requires_protected_runner")
    try:
        if runner.resolve() == Path(__file__).resolve():
            raise ReleaseCliError("release_profile_protected_runner_self_reference")
    except OSError:
        raise ReleaseCliError("release_profile_requires_protected_runner") from None
    child_env = dict(os.environ)
    for name in tuple(child_env):
        upper = name.upper()
        if name.endswith("_FD"):
            continue
        if any(
            marker in upper
            for marker in (
                "PASSWORD",
                "TOKEN",
                "SECRET",
                "PRIVATE_KEY",
                "API_KEY",
                "KEY_B64",
                "CREDENTIAL",
                "DATABASE_URL",
            )
        ):
            child_env.pop(name, None)
    child_env["MINDATLAS_RELEASE_RUN_DIR"] = str(args.run_dir)
    pass_fds: set[int] = set()
    for name in (
        "MINDATLAS_AUTOMATION_SIGNING_KEY_FD",
        "MINDATLAS_REHEARSAL_SIGNING_KEY_FD",
        "MINDATLAS_REHEARSAL_ATTEMPT_LEDGER_CREDENTIAL_FD",
    ):
        raw_fd = child_env.get(name)
        if raw_fd is None:
            continue
        try:
            descriptor = int(raw_fd)
        except (TypeError, ValueError):
            raise ReleaseCliError("release_secret_fd_unavailable") from None
        _require_fd(descriptor, code="release_secret_fd_unavailable")
        pass_fds.add(descriptor)
    try:
        result = subprocess.run(
            [str(runner), "--run-dir", str(args.run_dir), "--kind", str(state["kind"])],
            cwd=str(BACKEND_ROOT.parent),
            env=child_env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=60 * 60,
            check=False,
            pass_fds=tuple(sorted(pass_fds)),
        )
    except (OSError, subprocess.TimeoutExpired):
        raise ReleaseCliError("release_protected_runner_failed") from None
    if result.returncode != 0:
        raise ReleaseCliError("release_protected_runner_failed")
    verified = verify_profile(args)
    return {
        "kind": state["kind"],
        "state": "profile-evidence-produced",
        "objects": verified["objects"],
    }


def verify_profile(args: argparse.Namespace) -> dict[str, Any]:
    state = _require_prepared_run(args.run_dir)
    if str(getattr(args, "kind", "") or state.get("kind")) != state.get("kind"):
        raise ReleaseCliError("release_run_kind_mismatch")
    evidence = list(args.run_dir.glob("evidence/*.json"))
    # The rehearsal input OCI bundle lives at the run root. Evidence artifact
    # bundles are emitted below the protected `evidence/` directory so the two
    # immutable archives cannot be confused during offline verification.
    bundles = list(args.run_dir.glob("evidence/*.tar"))
    trust_set = args.run_dir / str(state.get("trustSet", "trust-set.json"))
    oci_bundle = args.run_dir / str(state.get("ociBundle", "release.oci.tar"))
    if (
        len(evidence) != 1
        or len(bundles) != 1
        or not trust_set.is_file()
        or not oci_bundle.is_file()
        or oci_bundle.is_symlink()
        or _file_digest(oci_bundle) != state.get("ociBundleDigest")
    ):
        raise ReleaseCliError("release_evidence_missing")
    try:
        from scripts.verify_release_attestation import verify

        summary, exit_code = verify(evidence[0], bundles[0], trust_set)
        manifest, _ = _verified_manifest(evidence[0], trust_set)
    except Exception:
        raise ReleaseCliError("release_evidence_verification_failed") from None
    if exit_code != 0 or summary.get("failedAssertions", 1) != 0:
        raise ReleaseCliError("release_evidence_assertions_failed")
    if (
        manifest.evidence_kind != state["kind"]
        or str(manifest.release_run_id) != str(state["runId"])
        or manifest.qualification_target_digest != state.get("qualificationTargetDigest")
        or manifest.scenario_set_digest != state.get("scenarioSetDigest")
        or manifest.required_assertion_set_digest != state.get("requiredAssertionSetDigest")
    ):
        raise ReleaseCliError("release_evidence_run_identity_mismatch")
    return {"kind": state["kind"], "state": "evidence_verified", "objects": 1}


def verify_complete_run(args: argparse.Namespace) -> dict[str, Any]:
    if getattr(args, "run_dir", None) is not None:
        state = _require_prepared_run(args.run_dir)
        marker = args.run_dir / "complete-run.json"
        if not marker.is_file():
            raise ReleaseCliError("release_complete_run_summary_missing")
        payload = _json_file(marker)
        if not isinstance(payload, dict) or payload.get("schemaVersion") != 1:
            raise ReleaseCliError("release_complete_run_summary_invalid")
        return {"kind": state["kind"], "state": "complete-run-summary-verified"}
    _require_file(args.qualification_target, code="release_qualification_target_missing")
    _require_file(args.automation_evidence, code="release_automation_evidence_missing")
    _require_file(args.rehearsal_evidence, code="release_rehearsal_evidence_missing")
    _require_file(args.scenario_set, code="release_scenario_set_missing")
    _require_file(args.required_e2e_module, code="release_required_e2e_module_missing")
    if not isinstance(args.source_revision, str) or not re.fullmatch(r"[0-9a-f]{40}", args.source_revision):
        raise ReleaseCliError("release_source_revision_invalid")
    if args.trust_set is None:
        raise ReleaseCliError("release_trust_set_missing")
    _require_file(args.trust_set, code="release_trust_set_missing")
    target = verify_target(args.qualification_target)
    try:
        from app.release.scenarios import load_scenario_set

        scenario_set = load_scenario_set(args.scenario_set)
        e2e_source = args.required_e2e_module.read_text(encoding="utf-8")
    except (OSError, UnicodeError, ValueError):
        raise ReleaseCliError("release_scenario_set_invalid") from None
    if any(marker in e2e_source.lower() for marker in ("pytest.skip", "pytest.mark.skip", "pytest.mark.xfail", "xfail(")):
        raise ReleaseCliError("release_required_e2e_skip_forbidden")
    # This command is an offline completeness gate, not a transcript replay.
    # The signed manifest verifier proves identity/signature and this command
    # proves the fixed scenario/assertion inventory was not silently replaced.
    for evidence_path, expected_kind in (
        (args.automation_evidence, "automated_qualification"),
        (args.rehearsal_evidence, "production_rehearsal"),
    ):
        manifest, _ = _verified_manifest(evidence_path, args.trust_set)
        if manifest.evidence_kind != expected_kind:
            raise ReleaseCliError("release_evidence_kind_mismatch")
        if manifest.build_revision != args.source_revision:
            raise ReleaseCliError("release_evidence_source_revision_mismatch")
        if manifest.qualification_target_digest != target["targetDigest"]:
            raise ReleaseCliError("release_evidence_target_mismatch")
        if manifest.scenario_set_digest != scenario_set.digest:
            raise ReleaseCliError("release_scenario_set_digest_mismatch")
        if manifest.required_assertion_set_digest != scenario_set.required_assertion_set_digest:
            raise ReleaseCliError("release_required_assertion_set_digest_mismatch")
        assertion_ids = {item.assertion_id for item in manifest.assertion_results}
        if not set(scenario_set.required_assertion_ids) <= assertion_ids:
            raise ReleaseCliError("release_evidence_assertion_inventory_incomplete")
    return {"state": "complete-run-evidence-present", "sourceRevision": args.source_revision}


def verify_target(path: Path) -> dict[str, Any]:
    from app.release.target_fixture import RehearsalInitializationFixtureV1
    from app.release.contracts import ReleaseQualificationTargetV1

    raw = _json_file(path)
    try:
        if isinstance(raw, dict) and "target" in raw and "fixtureDigest" in raw:
            fixture = RehearsalInitializationFixtureV1.model_validate(raw)
            target = fixture.target
            return {"targetDigest": target.qualification_target_digest, "fixtureDigest": fixture.fixture_digest}
        target = ReleaseQualificationTargetV1.model_validate(raw)
    except ValueError:
        raise ReleaseCliError("release_target_invalid") from None
    return {"targetDigest": target.qualification_target_digest}


def capture_target(args: argparse.Namespace) -> dict[str, Any]:
    parsed = urlparse(args.target_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ReleaseCliError("release_target_url_invalid")
    if (
        args.output.exists()
        or args.output.is_symlink()
        or args.provisioning_bundle.exists()
        or args.provisioning_bundle.is_symlink()
        or args.output.resolve(strict=False) == args.provisioning_bundle.resolve(strict=False)
    ):
        raise ReleaseCliError("release_target_output_must_not_exist")
    password = _read_fd_secret(
        args.operator_password_fd,
        code="release_operator_password_fd_unavailable",
    )
    try:
        client = _TargetHttpClient(args.target_url)
        client.login(password)
    finally:
        del password

    try:
        from app.release.contracts import ReleaseQualificationTargetV1
        from app.release.target_fixture import RehearsalInitializationFixtureV1

        target_payload = client.get("/api/pre-ga-launch/qualification-target")
        target = ReleaseQualificationTargetV1.model_validate(target_payload)
        # These are the authoritative read ports for the non-secret target
        # closure. Keep the endpoints fixed; a caller cannot select an arbitrary
        # package/profile subset and still receive a target-bound fixture.
        snapshots = {
            "profile": client.get("/api/assistant-config/main-agent-profiles/default"),
            "profileVersions": client.get("/api/assistant-config/main-agent-profiles/default/versions"),
            "skillPackages": client.get("/api/assistant-config/skill-packages?limit=200&offset=0"),
            "rollouts": client.get("/api/assistant-runtime/rollouts"),
        }
        provisioning = {
            "captureContractVersion": 1,
            "targetDigest": target.qualification_target_digest,
            "configuration": _sanitize_provisioning_configuration(snapshots),
        }
        fixture = RehearsalInitializationFixtureV1.build(
            target=target,
            provisioning=provisioning,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.provisioning_bundle.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(
            _canonical(target.model_dump(mode="json", by_alias=True)) + b"\n"
        )
        args.provisioning_bundle.write_bytes(
            _canonical(fixture.model_dump(mode="json", by_alias=True)) + b"\n"
        )
        args.output.chmod(0o600)
        args.provisioning_bundle.chmod(0o600)
    except ReleaseCliError:
        raise
    except (OSError, TypeError, ValueError):
        raise ReleaseCliError("release_target_capture_invalid") from None
    return {
        "state": "target-captured",
        "qualificationTargetDigest": target.qualification_target_digest,
        "fixtureDigest": fixture.fixture_digest,
    }


def compare_values(left: Path, right: Path) -> dict[str, Any]:
    left_payload = _json_file(left)
    right_payload = _json_file(right)
    left_digest = hashlib.sha256(_canonical(left_payload)).hexdigest()
    right_digest = hashlib.sha256(_canonical(right_payload)).hexdigest()
    if left_digest != right_digest:
        raise ReleaseCliError("release_identity_compare_mismatch")
    return {"canonicalDigest": left_digest}


def _verified_manifest(path: Path, trust_path: Path) -> tuple[Any, Any]:
    """Verify canonical manifest bytes/signature before identity comparison."""
    try:
        from app.release.evidence import verify_evidence_object
        from app.release.trust import load_trust_set
        from app.schema.canonical import canonical_json_bytes

        raw_bytes = path.read_bytes()
        payload = json.loads(raw_bytes.decode("utf-8"))
        if canonical_json_bytes(payload) != raw_bytes:
            raise ValueError
        manifest, attestation = verify_evidence_object(payload, load_trust_set(trust_path))
    except Exception:
        raise ReleaseCliError("release_evidence_verification_failed") from None
    if any(not item.passed for item in manifest.assertion_results):
        raise ReleaseCliError("release_evidence_assertions_failed")
    return manifest, attestation


def compare_evidence(automation_path: Path, rehearsal_path: Path, trust_path: Path) -> dict[str, Any]:
    automation, _ = _verified_manifest(automation_path, trust_path)
    rehearsal, _ = _verified_manifest(rehearsal_path, trust_path)
    if automation.evidence_kind != "automated_qualification" or rehearsal.evidence_kind != "production_rehearsal":
        raise ReleaseCliError("release_evidence_kind_mismatch")
    comparable_fields = (
        "qualification_target_digest",
        "build_revision",
        "image_set_digest",
        "deployed_artifact_set_digest",
        "schema_family",
        "schema_revision",
        "schema_application_fingerprint",
        "schema_control_fingerprint",
        "schema_identity_contract_version",
        "schema_contract_material_digest",
        "schema_deployment_class",
        "schema_seed_contract_digest",
        "schema_runtime_contract_version",
        "schema_checkpoint_codec_version",
        "schema_capability_feature_digest",
        "schema_runtime_identity_digest",
        "operator_auth_contract_version",
        "rollout_revision_id",
        "rollout_revision_digest",
        "runtime_closure_digest",
        "profile_version_id",
        "profile_content_digest",
        "model_id",
        "model_identity_digest",
        "package_closure_digest",
        "capability_closure_digest",
        "seed_manifest_digest",
        "worker_runtime_contract_version",
        "worker_checkpoint_codec_version",
        "worker_capability_feature_digest",
        "create_entry_contract_digest",
        "write_policy_digest",
        "write_cohort_digest",
        "reconciliation_contract_version",
        "dependency_lock_set_digest",
        "scenario_set_digest",
        "required_assertion_set_digest",
        "runner_contract_version",
        "runner_identity_digest",
        "evidence_trust_set_digest",
    )
    for field in comparable_fields:
        if getattr(automation, field) != getattr(rehearsal, field):
            raise ReleaseCliError("release_evidence_identity_compare_mismatch")
    if automation.qualification_infrastructure_identity != rehearsal.qualification_infrastructure_identity:
        raise ReleaseCliError("release_qualification_infrastructure_mismatch")
    return {
        "state": "evidence-identities-match",
        "qualificationTargetDigest": automation.qualification_target_digest,
        "automationManifestDigest": automation.manifest_digest,
        "rehearsalManifestDigest": rehearsal.manifest_digest,
    }


def verify_evidence_summary(
    *,
    summary_path: Path,
    evidence_path: Path | None = None,
    artifact_bundle: Path | None = None,
    trust_set: Path | None = None,
) -> dict[str, Any]:
    from app.release.contracts import SafeReleaseEvidenceSummaryV1

    raw_summary = _json_file(summary_path)
    try:
        summary = SafeReleaseEvidenceSummaryV1.model_validate(raw_summary)
    except ValueError:
        raise ReleaseCliError("release_evidence_summary_invalid") from None
    if evidence_path is None or artifact_bundle is None or trust_set is None:
        return {"state": "summary-shape-verified", "evidenceKind": summary.evidence_kind}
    _require_file(evidence_path, code="release_evidence_missing")
    _require_file(artifact_bundle, code="release_artifact_bundle_missing")
    _require_file(trust_set, code="release_trust_set_missing")
    try:
        from scripts.verify_release_attestation import verify
        from app.release.trust import attestation_object_digest

        verified, exit_code = verify(evidence_path, artifact_bundle, trust_set)
        evidence_payload = _json_file(evidence_path)
        manifest = evidence_payload["manifest"]
        attestation = evidence_payload["attestation"]
        attestation_digest = attestation_object_digest(attestation)
    except Exception:
        raise ReleaseCliError("release_evidence_verification_failed") from None
    if exit_code != 0 or verified.get("failedAssertions", 1) != 0:
        raise ReleaseCliError("release_evidence_assertions_failed")
    expected = {
        "evidenceKind": manifest.get("evidenceKind"),
        "releaseSourceRevision": manifest.get("buildRevision"),
        "qualificationTargetDigest": manifest.get("qualificationTargetDigest"),
        "manifestDigest": manifest.get("manifestDigest"),
        "attestationDigest": attestation_digest,
        "artifactAggregateDigest": manifest.get("artifactAggregateDigest"),
        "keyId": attestation.get("keyId") if isinstance(attestation, dict) else None,
        "assertionPassed": verified.get("passedAssertions"),
        "assertionFailed": verified.get("failedAssertions"),
    }
    actual = summary.model_dump(mode="json", by_alias=True)
    for key, value in expected.items():
        if actual.get(key) != value:
            raise ReleaseCliError("release_evidence_summary_mismatch")
    return {"state": "summary-verified", "evidenceKind": summary.evidence_kind}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the fixed MindAtlas pre-GA release profile")
    commands = parser.add_subparsers(dest="command", required=True)

    evidence = commands.add_parser("evidence")
    evidence_commands = evidence.add_subparsers(dest="evidence_command", required=True)
    run = evidence_commands.add_parser("run")
    run.add_argument("--kind", choices=("automated_qualification", "production_rehearsal"), required=True)
    run.add_argument("--profile-url", required=True)
    run.add_argument("--output-dir", type=Path, required=True)
    run.add_argument("--signing-key-fd", type=int, required=True)
    run.add_argument("--trust-set", type=Path, required=True)
    promote = evidence_commands.add_parser("promote")
    promote.add_argument("--evidence", type=Path, required=True)
    promote.add_argument("--artifact-bundle", type=Path, required=True)
    promote.add_argument("--trust-set", type=Path, required=True)
    promote.add_argument("--kind", choices=("automated_qualification", "production_rehearsal"))
    promote.add_argument("--target-alias", required=True)
    promote.add_argument("--destination-credential-fd", type=int)
    # Compatibility spelling for an older protected wrapper. The dispatch
    # path still requires an already-open descriptor, never a credential value.
    promote.add_argument("--credential-fd", type=int)

    profile = commands.add_parser("profile")
    profile_commands = profile.add_subparsers(dest="profile_command", required=True)
    validate = profile_commands.add_parser("validate-compose")
    validate.add_argument("--compose", type=Path, required=True)
    validate.add_argument("--image-lock", type=Path, required=True)
    prepare = profile_commands.add_parser("prepare")
    prepare.add_argument("--kind", choices=("automated_qualification", "production_rehearsal"), required=True)
    prepare.add_argument("--run-dir", type=Path, required=True)
    prepare.add_argument("--qualification-target", type=Path, required=True)
    prepare.add_argument("--target-provisioning-bundle", type=Path, required=True)
    prepare.add_argument("--signing-key-fd", type=int, required=True)
    prepare.add_argument("--trust-set", type=Path, required=True)
    prepare.add_argument("--automation-evidence", type=Path)
    prepare.add_argument("--oci-bundle", type=Path)
    prepare.add_argument("--attempt-ledger-alias")
    prepare.add_argument("--attempt-ledger-credential-fd", type=int)
    prepare.add_argument("--no-build", action="store_true")
    for name in ("run", "verify"):
        command = profile_commands.add_parser(name)
        command.add_argument("--kind", choices=("automated_qualification", "production_rehearsal"), required=False)
        command.add_argument("--run-dir", type=Path, required=True)
        command.add_argument("--automation-evidence", type=Path)
        command.add_argument("--oci-bundle", type=Path)
        command.add_argument("--no-build", action="store_true")
    complete = profile_commands.add_parser("verify-complete-run")
    complete.add_argument("--qualification-target", type=Path, required=True)
    complete.add_argument("--automation-evidence", type=Path, required=True)
    complete.add_argument("--rehearsal-evidence", type=Path, required=True)
    complete.add_argument("--scenario-set", type=Path, required=True)
    complete.add_argument("--required-e2e-module", type=Path, required=True)
    complete.add_argument("--source-revision", required=True)
    complete.add_argument("--trust-set", type=Path)

    target = commands.add_parser("target")
    target_commands = target.add_subparsers(dest="target_command", required=True)
    capture = target_commands.add_parser("capture")
    capture.add_argument("--base-url", "--target-url", dest="target_url", required=True)
    capture.add_argument("--operator-password-fd", type=int, required=True)
    capture.add_argument("--qualification-target", "--output", dest="output", type=Path, required=True)
    capture.add_argument("--provisioning-bundle", type=Path, required=True)
    verify = target_commands.add_parser("verify")
    verify.add_argument("--target", type=Path)
    verify.add_argument("--base-url")
    verify.add_argument("--qualification-target", type=Path)
    verify.add_argument("--automation-evidence", type=Path)
    verify.add_argument("--rehearsal-evidence", type=Path)

    compare = commands.add_parser("compare")
    compare.add_argument("--left", type=Path)
    compare.add_argument("--right", type=Path)
    compare.add_argument("--automation-evidence", type=Path)
    compare.add_argument("--rehearsal-evidence", type=Path)
    compare.add_argument("--trust-set", type=Path)

    artifact = commands.add_parser("artifact")
    artifact_commands = artifact.add_subparsers(dest="artifact_command", required=True)
    build = artifact_commands.add_parser("build")
    build.add_argument("--source-revision", required=True)
    build.add_argument("--output-dir", type=Path, required=True)
    build.add_argument("--no-build", action="store_true")
    artifact_verify = artifact_commands.add_parser("verify")
    artifact_verify.add_argument("--deployment-identity", type=Path, required=True)
    artifact_verify.add_argument("--oci-bundle", type=Path, required=True)
    artifact_verify.add_argument("--run-dir", type=Path)

    launch = commands.add_parser("launch")
    launch_command = launch.add_subparsers(dest="launch_command", required=True).add_parser("verify")
    launch_command.add_argument("--base-url")
    launch_command.add_argument("--summary", type=Path, required=True)
    launch_command.add_argument("--target", type=Path)
    launch_command.add_argument("--candidate", type=Path)

    clone = commands.add_parser("production-clone")
    clone_command = clone.add_subparsers(dest="clone_command", required=True).add_parser("negative-acceptance")
    clone_command.add_argument("--source-database-url-fd", "--source-url-fd", dest="source_url_fd", type=int, required=True)
    clone_command.add_argument("--run-dir", type=Path, required=True)
    clone_command.add_argument("--qualification-target", type=Path, required=True)
    clone_command.add_argument("--automation-evidence", type=Path, required=True)
    clone_command.add_argument("--rehearsal-evidence", type=Path, required=True)
    clone_command.add_argument("--oci-bundle", type=Path, required=True)

    summary = commands.add_parser("evidence-summary")
    summary_command = summary.add_subparsers(dest="summary_command", required=True).add_parser("check")
    summary_command.add_argument("--summary", type=Path, required=True)
    summary_command.add_argument("--evidence", type=Path)
    summary_command.add_argument("--artifact-bundle", type=Path)
    summary_command.add_argument("--trust-set", type=Path)

    return parser


def dispatch(args: argparse.Namespace) -> dict[str, Any]:
    if args.command == "evidence" and args.evidence_command == "run":
        _validate_evidence_run(args)
        raise ReleaseCliError("release_runner_requires_server_profile")
    if args.command == "evidence" and args.evidence_command == "promote":
        promotion_fd = (
            args.destination_credential_fd
            if args.destination_credential_fd is not None
            else args.credential_fd
        )
        _require_fd(promotion_fd, code="release_promotion_credential_fd_unavailable")
        _require_file(args.evidence, code="release_evidence_missing")
        _require_file(args.artifact_bundle, code="release_artifact_bundle_missing")
        _require_file(args.trust_set, code="release_trust_set_missing")
        _require_safe_alias(args.target_alias)
        if args.kind is not None:
            try:
                payload = _json_file(args.evidence)
                observed_kind = payload.get("manifest", {}).get("evidenceKind")
            except (AttributeError, ReleaseCliError):
                raise ReleaseCliError("release_evidence_invalid") from None
            if observed_kind != args.kind:
                raise ReleaseCliError("release_evidence_kind_mismatch")
        raise ReleaseCliError("release_evidence_promotion_requires_verified_host_runner")
    if args.command == "profile":
        if args.profile_command == "validate-compose":
            return validate_compose(args.compose, args.image_lock)
        if args.profile_command == "prepare":
            return prepare_profile(args)
        if args.profile_command == "run":
            return run_profile(args)
        if args.profile_command == "verify":
            return verify_profile(args)
        if args.profile_command == "verify-complete-run":
            return verify_complete_run(args)
    if args.command == "target":
        if args.target_command == "capture":
            return capture_target(args)
        if args.target_command == "verify":
            if args.target is not None:
                return verify_target(args.target)
            _require_file(args.qualification_target, code="release_qualification_target_missing")
            _require_file(args.automation_evidence, code="release_automation_evidence_missing")
            _require_file(args.rehearsal_evidence, code="release_rehearsal_evidence_missing")
            parsed = urlparse(args.base_url or "")
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ReleaseCliError("release_target_url_invalid")
            raise ReleaseCliError("release_target_verification_requires_authenticated_runner")
    if args.command == "compare":
        if args.left is not None and args.right is not None:
            return compare_values(args.left, args.right)
        _require_file(args.automation_evidence, code="release_automation_evidence_missing")
        _require_file(args.rehearsal_evidence, code="release_rehearsal_evidence_missing")
        trust_path = args.trust_set
        if trust_path is None:
            configured = os.environ.get("MINDATLAS_RELEASE_TRUST_SET_FILE", "")
            trust_path = Path(configured) if configured else None
        _require_file(trust_path, code="release_trust_set_missing")
        return compare_evidence(args.automation_evidence, args.rehearsal_evidence, trust_path)
    if args.command == "artifact":
        if args.artifact_command == "build":
            if not re.fullmatch(r"[0-9a-f]{40}", args.source_revision):
                raise ReleaseCliError("release_source_revision_invalid")
            if args.output_dir.exists() and any(args.output_dir.iterdir()):
                raise ReleaseCliError("release_artifact_output_dir_must_be_empty")
            if not args.no_build:
                raise ReleaseCliError("release_artifact_requires_explicit_no_build_boundary")
            raise ReleaseCliError("release_artifact_build_requires_protected_runner")
        _require_file(args.deployment_identity, code="release_deployment_identity_missing")
        _require_file(args.oci_bundle, code="release_oci_bundle_missing")
        raise ReleaseCliError("release_artifact_verification_requires_protected_runner")
    if args.command == "launch":
        _require_file(args.summary, code="release_launch_summary_missing")
        if args.base_url is not None:
            parsed = urlparse(args.base_url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ReleaseCliError("release_target_url_invalid")
        raise ReleaseCliError("release_launch_verification_requires_server_state")
    if args.command == "production-clone":
        _require_fd(args.source_url_fd, code="release_source_url_fd_unavailable")
        _require_file(args.qualification_target, code="release_qualification_target_missing")
        _require_file(args.automation_evidence, code="release_automation_evidence_missing")
        _require_file(args.rehearsal_evidence, code="release_rehearsal_evidence_missing")
        _require_file(args.oci_bundle, code="release_oci_bundle_missing")
        if args.run_dir.exists():
            raise ReleaseCliError("release_clone_run_dir_must_not_exist")
        raise ReleaseCliError("production_clone_requires_protected_postgres_runner")
    if args.command == "evidence-summary":
        supplied = (args.evidence, args.artifact_bundle, args.trust_set)
        if any(item is not None for item in supplied) and not all(item is not None for item in supplied):
            raise ReleaseCliError("release_evidence_summary_inputs_incomplete")
        return verify_evidence_summary(
            summary_path=args.summary,
            evidence_path=args.evidence,
            artifact_bundle=args.artifact_bundle,
            trust_set=args.trust_set,
        )
    raise ReleaseCliError("release_command_invalid")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = dispatch(args)
    except ReleaseCliError as exc:
        print(exc.safe_code)
        return 3
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
