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
import stat
import subprocess
import sys
import tarfile
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


def _require_fd(fd: int | None, *, code: str = "release_secret_fd_unavailable") -> None:
    if not isinstance(fd, int) or isinstance(fd, bool) or fd < 0:
        raise ReleaseCliError(code)
    try:
        os.fstat(fd)
    except OSError:
        raise ReleaseCliError(code) from None


def _require_file(path: Path | None, *, code: str) -> Path:
    if not isinstance(path, Path) or not path.is_file():
        raise ReleaseCliError(code)
    return path


def _require_regular_file(path: Path | None, *, code: str) -> Path:
    required = _require_file(path, code=code)
    if required.is_symlink():
        raise ReleaseCliError(code)
    try:
        if not stat.S_ISREG(required.stat().st_mode):
            raise ReleaseCliError(code)
    except OSError:
        raise ReleaseCliError(code) from None
    return required


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
    _require_regular_file(args.qualification_target, code="release_qualification_target_missing")
    _require_regular_file(
        args.target_provisioning_bundle,
        code="release_target_provisioning_bundle_missing",
    )
    material = _validate_target_material(args.qualification_target, args.target_provisioning_bundle)
    _require_fd(args.signing_key_fd, code="release_signing_key_fd_unavailable")
    _require_regular_file(args.trust_set, code="release_trust_set_missing")
    oci_bundle = getattr(args, "oci_bundle", None)
    if oci_bundle is None:
        configured_bundle = os.environ.get("MINDATLAS_RELEASE_OCI_BUNDLE", "")
        oci_bundle = Path(configured_bundle) if configured_bundle else None
    oci_bundle = _require_regular_file(oci_bundle, code="release_oci_bundle_missing")
    deployment_identity = getattr(args, "deployment_identity", None)
    if deployment_identity is None:
        configured_identity = os.environ.get("MINDATLAS_RELEASE_DEPLOYMENT_IDENTITY_FILE", "")
        deployment_identity = Path(configured_identity) if configured_identity else None
    deployment_identity = _require_regular_file(
        deployment_identity,
        code="release_deployment_identity_missing",
    )
    try:
        from app.release.trust import load_trust_set

        load_trust_set(args.trust_set)
    except Exception:
        raise ReleaseCliError("release_trust_set_invalid") from None
    if not bool(getattr(args, "no_build", False)):
        raise ReleaseCliError("release_profile_requires_no_build")
    artifact_verification = verify_artifact(
        argparse.Namespace(
            deployment_identity=deployment_identity,
            oci_bundle=oci_bundle,
            trust_set=args.trust_set,
            run_dir=None,
        )
    )
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
        raw_attempt_fd = getattr(args, "attempt_ledger_credential_fd", None)
        try:
            attempt_fd = int(raw_attempt_fd) if raw_attempt_fd is not None else -1
        except (TypeError, ValueError):
            raise ReleaseCliError("release_attempt_ledger_credential_fd_unavailable") from None
        _require_fd(attempt_fd, code="release_attempt_ledger_credential_fd_unavailable")
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
    shutil.copyfile(deployment_identity, args.run_dir / "deployment-identity.json")
    shutil.copyfile(args.trust_set, args.run_dir / "trust-set.json")
    for name in (
        "qualification-target.json",
        "target-provisioning.bundle",
        "release.oci.tar",
        "deployment-identity.json",
    ):
        (args.run_dir / name).chmod(0o600)
    (args.run_dir / "deployment-identity.json").chmod(0o444)
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
        "deploymentIdentity": "deployment-identity.json",
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
        "deploymentIdentityDigest": _file_digest(deployment_identity),
        "imageSetDigest": artifact_verification["imageSetDigest"],
        "deployedArtifactSetDigest": artifact_verification["deployedArtifactSetDigest"],
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
    deployment_identity = _require_regular_file(
        args.run_dir / str(state.get("deploymentIdentity", "deployment-identity.json")),
        code="release_deployment_identity_missing",
    )
    if _file_digest(deployment_identity) != state.get("deploymentIdentityDigest"):
        raise ReleaseCliError("release_deployment_identity_drift")
    supplied_oci = getattr(args, "oci_bundle", None)
    if supplied_oci is None:
        configured_bundle = os.environ.get("MINDATLAS_RELEASE_OCI_BUNDLE", "")
        supplied_oci = Path(configured_bundle) if configured_bundle else None
    if supplied_oci is None:
        supplied_oci = args.run_dir / str(state.get("ociBundle", "release.oci.tar"))
    supplied_oci = _require_regular_file(supplied_oci, code="release_oci_bundle_missing")
    if _file_digest(supplied_oci) != state.get("ociBundleDigest"):
        raise ReleaseCliError("release_oci_bundle_drift")
    try:
        artifact_verification = verify_artifact(
            argparse.Namespace(
                deployment_identity=deployment_identity,
                oci_bundle=supplied_oci,
                trust_set=trust_path,
                run_dir=None,
            )
        )
    except ReleaseCliError:
        raise ReleaseCliError("release_deployment_artifact_drift") from None
    if (
        artifact_verification["imageSetDigest"] != state.get("imageSetDigest")
        or artifact_verification["deployedArtifactSetDigest"]
        != state.get("deployedArtifactSetDigest")
    ):
        raise ReleaseCliError("release_deployment_artifact_drift")
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
    trust_set = _require_regular_file(
        args.run_dir / str(state.get("trustSet", "trust-set.json")),
        code="release_evidence_missing",
    )
    identity = _require_regular_file(
        args.run_dir / str(state.get("deploymentIdentity", "deployment-identity.json")),
        code="release_evidence_missing",
    )
    oci_bundle = args.run_dir / str(state.get("ociBundle", "release.oci.tar"))
    if (
        len(evidence) != 1
        or len(bundles) != 1
        or evidence[0].is_symlink()
        or bundles[0].is_symlink()
        or not oci_bundle.is_file()
        or oci_bundle.is_symlink()
        or _file_digest(oci_bundle) != state.get("ociBundleDigest")
    ):
        raise ReleaseCliError("release_evidence_missing")
    try:
        artifact_verification = verify_artifact(
            argparse.Namespace(
                deployment_identity=identity,
                oci_bundle=oci_bundle,
                trust_set=trust_set,
                run_dir=None,
            )
        )
    except ReleaseCliError:
        raise ReleaseCliError("release_deployment_artifact_drift") from None
    if (
        artifact_verification["imageSetDigest"] != state.get("imageSetDigest")
        or artifact_verification["deployedArtifactSetDigest"]
        != state.get("deployedArtifactSetDigest")
    ):
        raise ReleaseCliError("release_deployment_artifact_drift")
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


def _run_checked(
    command: list[str],
    *,
    cwd: Path,
    safe_code: str,
    env: dict[str, str] | None = None,
    timeout: int = 3600,
) -> subprocess.CompletedProcess[str]:
    """Run a build/inspection command without forwarding its output."""

    try:
        result = subprocess.run(
            command,
            cwd=str(cwd),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise ReleaseCliError(safe_code) from None
    if result.returncode != 0:
        raise ReleaseCliError(safe_code)
    return result


def _source_revision_is_clean(source_revision: str) -> None:
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(BACKEND_ROOT.parent),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        )
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=str(BACKEND_ROOT.parent),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        )
    except OSError:
        raise ReleaseCliError("release_source_state_unavailable") from None
    if revision.returncode != 0 or revision.stdout.strip() != source_revision:
        raise ReleaseCliError("release_source_revision_mismatch")
    if status.returncode != 0 or status.stdout:
        raise ReleaseCliError("release_source_tree_dirty")


def _frontend_content_digest() -> str:
    frontend_root = BACKEND_ROOT.parent / "frontend"
    npm = shutil.which("npm")
    node = shutil.which("node")
    digest_script = frontend_root / "scripts" / "compute-build-content-digest.mjs"
    if npm is None or node is None or not digest_script.is_file():
        raise ReleaseCliError("release_frontend_build_tool_unavailable")
    _run_checked([npm, "ci"], cwd=frontend_root, safe_code="release_frontend_build_failed")
    _run_checked([npm, "run", "build"], cwd=frontend_root, safe_code="release_frontend_build_failed")
    result = _run_checked(
        [node, str(digest_script), str(frontend_root / "dist")],
        cwd=frontend_root,
        safe_code="release_frontend_digest_failed",
    )
    digest = result.stdout.strip()
    if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise ReleaseCliError("release_frontend_digest_invalid")
    return digest


def _deployment_signing_inputs(args: argparse.Namespace) -> tuple[int, str]:
    raw_fd = getattr(args, "signing_key_fd", None)
    if raw_fd is None:
        raw_fd = os.environ.get("MINDATLAS_DEPLOYMENT_SIGNING_KEY_FD", "")
    try:
        fd = int(raw_fd)
    except (TypeError, ValueError):
        raise ReleaseCliError("release_deployment_signing_fd_unavailable") from None
    _require_fd(fd, code="release_deployment_signing_fd_unavailable")
    key_id = getattr(args, "deployment_key_id", None) or os.environ.get(
        "MINDATLAS_DEPLOYMENT_SIGNING_KEY_ID", ""
    )
    if not isinstance(key_id, str) or _SAFE_ID.fullmatch(key_id) is None:
        raise ReleaseCliError("release_deployment_key_id_invalid")
    return fd, key_id


def _write_private_output(path: Path, data: bytes, *, mode: int = 0o600) -> None:
    if path.exists() or path.is_symlink():
        raise ReleaseCliError("release_artifact_output_collision")
    path.write_bytes(data)
    path.chmod(mode)


def build_artifact(args: argparse.Namespace) -> dict[str, Any]:
    """Build and seal the exact application image bundle once."""

    source_revision = str(getattr(args, "source_revision", ""))
    if re.fullmatch(r"[0-9a-f]{40}", source_revision) is None:
        raise ReleaseCliError("release_source_revision_invalid")
    if bool(getattr(args, "no_build", False)):
        raise ReleaseCliError("release_artifact_build_requires_build")
    output_dir = getattr(args, "output_dir", None)
    if not isinstance(output_dir, Path) or output_dir.exists():
        raise ReleaseCliError("release_artifact_output_dir_must_not_exist")
    docker = shutil.which("docker")
    if docker is None:
        raise ReleaseCliError("release_artifact_docker_unavailable")
    _source_revision_is_clean(source_revision)
    signing_fd, key_id = _deployment_signing_inputs(args)
    frontend_digest = _frontend_content_digest()
    from app.release.generated_lock_digests import (
        API_WORKER_LOCK_SHA256,
        DEPENDENCY_LOCK_SET_SHA256,
        PARSE_WORKER_LOCK_SHA256,
    )

    output_dir.mkdir(parents=True, mode=0o700)
    tag_suffix = source_revision
    backend_tag = f"mindatlas-release-backend:{tag_suffix}"
    scripted_provider_tag = f"mindatlas-release-scripted-provider:{tag_suffix}"
    web_tag = f"mindatlas-release-web:{tag_suffix}"
    common_args = [
        "--platform",
        "linux/amd64",
        "--load",
        "--build-arg",
        f"APP_BUILD_REVISION={source_revision}",
        "--build-arg",
        f"API_WORKER_LOCK_SHA256={API_WORKER_LOCK_SHA256}",
        "--build-arg",
        f"PARSE_WORKER_LOCK_SHA256={PARSE_WORKER_LOCK_SHA256}",
        "--build-arg",
        f"DEPENDENCY_LOCK_SET_SHA256={DEPENDENCY_LOCK_SET_SHA256}",
    ]
    _run_checked(
        [docker, "buildx", "build", *common_args, "--target", "runtime", "--tag", backend_tag, str(BACKEND_ROOT)],
        cwd=BACKEND_ROOT.parent,
        safe_code="release_backend_image_build_failed",
    )
    _run_checked(
        [docker, "buildx", "build", *common_args, "--target", "scripted-provider", "--tag", scripted_provider_tag, str(BACKEND_ROOT)],
        cwd=BACKEND_ROOT.parent,
        safe_code="release_scripted_provider_image_build_failed",
    )
    _run_checked(
        [
            docker,
            "buildx",
            "build",
            "--platform",
            "linux/amd64",
            "--load",
            "--build-arg",
            f"APP_BUILD_REVISION={source_revision}",
            "--build-arg",
            f"FRONTEND_BUILD_CONTENT_DIGEST={frontend_digest}",
            "--tag",
            web_tag,
            str(BACKEND_ROOT.parent / "frontend"),
        ],
        cwd=BACKEND_ROOT.parent,
        safe_code="release_web_image_build_failed",
    )
    bundle_path = output_dir / "release-application-images.tar"
    inspection_path = output_dir / "release-image-inspection.json"
    identity_path = output_dir / "deployment-identity.json"
    _run_checked(
        [docker, "save", "--output", str(bundle_path), backend_tag, scripted_provider_tag, web_tag],
        cwd=BACKEND_ROOT.parent,
        safe_code="release_oci_bundle_export_failed",
    )
    inspection = _run_checked(
        [docker, "image", "inspect", backend_tag, scripted_provider_tag, web_tag],
        cwd=BACKEND_ROOT.parent,
        safe_code="release_image_inspection_failed",
    )
    try:
        raw_inspection = json.loads(inspection.stdout)
    except (UnicodeError, ValueError):
        raise ReleaseCliError("release_image_inspection_invalid") from None
    try:
        from scripts.project_release_image_inspection import project

        normalized_inspection = project(raw_inspection, build_revision=source_revision)
    except ValueError as exc:
        raise ReleaseCliError(getattr(exc, "safe_code", "release_image_inspection_invalid")) from None
    _write_private_output(inspection_path, _canonical(normalized_inspection) + b"\n")
    provider_entry = next(
        (
            item
            for item in normalized_inspection
            if item["RepoTags"][0] == scripted_provider_tag
        ),
        None,
    )
    if not isinstance(provider_entry, dict) or not isinstance(provider_entry.get("imageDigest"), str):
        raise ReleaseCliError("release_scripted_provider_image_identity_missing")
    provider_digest = str(provider_entry["imageDigest"]).removeprefix("sha256:")
    from scripts.render_release_deployment_identity import render

    render(
        input_path=inspection_path,
        output_path=identity_path,
        build_revision=source_revision,
        dependency_lock_set_digest=DEPENDENCY_LOCK_SET_SHA256,
        key_id=key_id,
        signing_key_fd=signing_fd,
    )
    bundle_digest = _file_digest(bundle_path)
    identity_payload = _json_file(identity_path)
    identity = identity_payload.get("identity") if isinstance(identity_payload, dict) else None
    if not isinstance(identity, dict):
        raise ReleaseCliError("release_deployment_identity_invalid")
    state = {
        "schemaVersion": 1,
        "sourceRevision": source_revision,
        "platform": "linux/amd64",
        "applicationImages": [backend_tag, scripted_provider_tag, web_tag],
        "bundleFile": bundle_path.name,
        "bundleDigest": bundle_digest,
        "deploymentIdentityFile": identity_path.name,
        "imageSetDigest": identity.get("imageSetDigest"),
        "deployedArtifactSetDigest": identity.get("deployedArtifactSetDigest"),
        "dependencyLockSetDigest": DEPENDENCY_LOCK_SET_SHA256,
        "frontendBuildContentDigest": frontend_digest,
        "scriptedProviderImageDigest": provider_digest,
    }
    _write_private_output(output_dir / "artifact-state.json", _canonical(state) + b"\n")
    identity_path.chmod(0o444)
    return {
        "state": "artifact-built",
        "sourceRevision": source_revision,
        "bundleDigest": bundle_digest,
        "imageSetDigest": identity.get("imageSetDigest"),
        "deployedArtifactSetDigest": identity.get("deployedArtifactSetDigest"),
        "scriptedProviderImageDigest": provider_digest,
    }


def _validate_archive_bundle(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise ReleaseCliError("release_oci_bundle_must_be_regular_file")
    members = []
    try:
        with tarfile.open(path, mode="r") as archive:
            for member in archive.getmembers():
                name = member.name
                if name.startswith("/") or ".." in Path(name).parts:
                    raise ReleaseCliError("release_oci_bundle_member_invalid")
                if member.issym() or member.islnk() or not (member.isfile() or member.isdir()):
                    raise ReleaseCliError("release_oci_bundle_member_invalid")
                members.append(name)
    except ReleaseCliError:
        raise
    except (OSError, tarfile.TarError):
        raise ReleaseCliError("release_oci_bundle_invalid") from None
    if not members or "manifest.json" not in members:
        raise ReleaseCliError("release_oci_bundle_manifest_missing")
    return {"memberCount": len(members), "bundleDigest": _file_digest(path)}


def _validate_oci_image_bundle(path: Path, identity: Any) -> dict[str, Any]:
    """Verify a Docker-save archive is the exact three-image release export."""

    if path.is_symlink():
        raise ReleaseCliError("release_oci_bundle_must_be_regular_file")
    try:
        with tarfile.open(path, mode="r") as archive:
            members = archive.getmembers()
            by_name: dict[str, tarfile.TarInfo] = {}
            for member in members:
                if (
                    member.name in by_name
                    or member.name.startswith("/")
                    or ".." in Path(member.name).parts
                    or member.issym()
                    or member.islnk()
                    or not (member.isfile() or member.isdir())
                ):
                    raise ReleaseCliError("release_oci_bundle_member_invalid")
                by_name[member.name] = member
            manifest_member = by_name.get("manifest.json")
            if manifest_member is None:
                raise ReleaseCliError("release_oci_bundle_manifest_missing")
            handle = archive.extractfile(manifest_member)
            if handle is None:
                raise ReleaseCliError("release_oci_bundle_manifest_invalid")
            try:
                manifest = json.loads(handle.read().decode("utf-8"))
            except (UnicodeError, ValueError):
                raise ReleaseCliError("release_oci_bundle_manifest_invalid") from None
            if not isinstance(manifest, list):
                raise ReleaseCliError("release_oci_bundle_image_inventory_invalid")
            expected_tags = {
                f"mindatlas-release-backend:{identity.build_revision}": "backend",
                f"mindatlas-release-scripted-provider:{identity.build_revision}": "scripted-provider",
                f"mindatlas-release-web:{identity.build_revision}": "web",
            }
            images: dict[str, dict[str, Any]] = {}
            for item in manifest:
                if not isinstance(item, dict):
                    raise ReleaseCliError("release_oci_bundle_image_inventory_invalid")
                tags = item.get("RepoTags")
                if not isinstance(tags, list) or len(tags) != 1 or tags[0] not in expected_tags:
                    raise ReleaseCliError("release_oci_bundle_image_inventory_invalid")
                tag = tags[0]
                role = expected_tags[tag]
                if role in images:
                    raise ReleaseCliError("release_oci_bundle_image_inventory_invalid")
                config_name = item.get("Config")
                if not isinstance(config_name, str) or re.fullmatch(r"[0-9a-f]{64}\.json", config_name) is None:
                    raise ReleaseCliError("release_oci_bundle_config_invalid")
                config_member = by_name.get(config_name)
                if config_member is None:
                    raise ReleaseCliError("release_oci_bundle_config_missing")
                config_handle = archive.extractfile(config_member)
                if config_handle is None:
                    raise ReleaseCliError("release_oci_bundle_config_invalid")
                config_bytes = config_handle.read()
                config_digest = hashlib.sha256(config_bytes).hexdigest()
                if config_name != f"{config_digest}.json":
                    raise ReleaseCliError("release_oci_bundle_config_digest_invalid")
                try:
                    config = json.loads(config_bytes.decode("utf-8"))
                except (UnicodeError, ValueError):
                    raise ReleaseCliError("release_oci_bundle_config_invalid") from None
                labels = config.get("config", {}).get("Labels", {}) if isinstance(config, dict) else {}
                if not isinstance(labels, dict):
                    raise ReleaseCliError("release_oci_bundle_labels_invalid")
                if (
                    labels.get("org.opencontainers.image.revision") != identity.build_revision
                    or labels.get("io.mindatlas.platform") != "linux/amd64"
                ):
                    raise ReleaseCliError("release_oci_bundle_identity_label_mismatch")
                if role in {"backend", "scripted-provider"} and labels.get(
                    "io.mindatlas.dependency-lock-set-sha256"
                ) != identity.dependency_lock_set_digest:
                    raise ReleaseCliError("release_oci_bundle_lock_label_mismatch")
                if role == "web" and re.fullmatch(
                    r"[0-9a-f]{64}",
                    str(labels.get("io.mindatlas.frontend-build-content-sha256", "")),
                ) is None:
                    raise ReleaseCliError("release_oci_bundle_frontend_label_invalid")
                layers = item.get("Layers")
                if not isinstance(layers, list) or not layers:
                    raise ReleaseCliError("release_oci_bundle_layers_invalid")
                for layer_name in layers:
                    if (
                        not isinstance(layer_name, str)
                        or layer_name not in by_name
                        or not by_name[layer_name].isfile()
                    ):
                        raise ReleaseCliError("release_oci_bundle_layer_missing")
                images[role] = {
                    "configDigest": config_digest,
                    "layerCount": len(layers),
                }
            if set(images) != {"backend", "scripted-provider", "web"}:
                raise ReleaseCliError("release_oci_bundle_image_inventory_invalid")
            if (
                identity.api_image_digest != images["backend"]["configDigest"]
                or identity.assistant_worker_image_digest != images["backend"]["configDigest"]
                or identity.web_image_digest != images["web"]["configDigest"]
            ):
                raise ReleaseCliError("release_oci_bundle_identity_digest_mismatch")
            return {
                "memberCount": len(members),
                "bundleDigest": _file_digest(path),
                "images": images,
            }
    except ReleaseCliError:
        raise
    except (OSError, tarfile.TarError):
        raise ReleaseCliError("release_oci_bundle_invalid") from None


def verify_artifact(args: argparse.Namespace) -> dict[str, Any]:
    deployment_identity = _require_regular_file(
        args.deployment_identity,
        code="release_deployment_identity_missing",
    )
    oci_bundle = _require_regular_file(args.oci_bundle, code="release_oci_bundle_missing")
    trust_path = getattr(args, "trust_set", None)
    if trust_path is None:
        configured = os.environ.get("MINDATLAS_RELEASE_TRUST_SET_FILE", "")
        trust_path = Path(configured) if configured else None
    trust_path = _require_regular_file(trust_path, code="release_trust_set_missing")
    try:
        from app.release.trust import load_trust_set, verify_deployed_artifact_identity

        identity_payload = _json_file(deployment_identity)
        identity = verify_deployed_artifact_identity(identity_payload, load_trust_set(trust_path))
    except Exception:
        raise ReleaseCliError("release_deployment_identity_verification_failed") from None
    archive = _validate_oci_image_bundle(oci_bundle, identity)
    run_dir = getattr(args, "run_dir", None)
    if isinstance(run_dir, Path):
        state_path = run_dir / "artifact-state.json"
        if state_path.is_file():
            state = _json_file(state_path)
            if not isinstance(state, dict) or state.get("bundleDigest") != archive["bundleDigest"]:
                raise ReleaseCliError("release_artifact_state_mismatch")
            if state.get("deployedArtifactSetDigest") != identity.deployed_artifact_set_digest:
                raise ReleaseCliError("release_artifact_state_mismatch")
            if state.get("scriptedProviderImageDigest") != archive["images"]["scripted-provider"]["configDigest"]:
                raise ReleaseCliError("release_artifact_state_mismatch")
    return {
        "state": "artifact-verified",
        "bundleDigest": archive["bundleDigest"],
        "memberCount": archive["memberCount"],
        "imageSetDigest": identity.image_set_digest,
        "deployedArtifactSetDigest": identity.deployed_artifact_set_digest,
        "scriptedProviderImageDigest": archive["images"]["scripted-provider"]["configDigest"],
    }


def _consume_descriptor(fd: int, *, code: str) -> None:
    """Validate an inherited credential descriptor without reading its secret."""

    _require_fd(fd, code=code)
    try:
        os.fstat(fd)
    except OSError:
        raise ReleaseCliError(code) from None


def _conditional_create(path: Path, content: bytes, *, root: Path) -> bool:
    """Create an append-only object, accepting only an identical replay."""

    if root.is_symlink() or path.is_symlink():
        raise ReleaseCliError("release_promotion_object_collision")
    try:
        relative = path.relative_to(root)
    except ValueError:
        raise ReleaseCliError("release_promotion_object_collision") from None
    current = root
    if current.exists() and not current.is_dir():
        raise ReleaseCliError("release_promotion_object_collision")
    current.mkdir(parents=True, exist_ok=True)
    for part in relative.parts[:-1]:
        current = current / part
        if current.is_symlink() or (current.exists() and not current.is_dir()):
            raise ReleaseCliError("release_promotion_object_collision")
        current.mkdir(exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    except FileExistsError:
        try:
            existing = path.read_bytes()
        except OSError:
            raise ReleaseCliError("release_promotion_object_unreadable") from None
        if existing != content:
            raise ReleaseCliError("release_promotion_object_collision")
        return True
    except OSError:
        raise ReleaseCliError("release_promotion_object_write_failed") from None
    try:
        view = memoryview(content)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError("short object write")
            view = view[written:]
        os.fchmod(fd, 0o444)
    except OSError:
        raise ReleaseCliError("release_promotion_object_write_failed") from None
    finally:
        os.close(fd)
    return False


def _read_bundle_artifacts(bundle_path: Path, manifest: Any) -> dict[str, bytes]:
    expected = {item.sha256_digest: item for item in manifest.artifact_refs}
    seen: dict[str, bytes] = {}
    try:
        with tarfile.open(bundle_path, mode="r") as archive:
            for member in archive.getmembers():
                if (
                    not member.isfile()
                    or member.issym()
                    or member.islnk()
                    or re.fullmatch(r"artifacts/[0-9a-f]{64}", member.name) is None
                ):
                    raise ReleaseCliError("release_artifact_bundle_member_invalid")
                digest = member.name.split("/", 1)[1]
                if digest in seen or digest not in expected:
                    raise ReleaseCliError("release_artifact_bundle_inventory_invalid")
                handle = archive.extractfile(member)
                if handle is None:
                    raise ReleaseCliError("release_artifact_bundle_member_unreadable")
                content = handle.read()
                if hashlib.sha256(content).hexdigest() != digest or len(content) != expected[digest].byte_size:
                    raise ReleaseCliError("release_artifact_bundle_digest_invalid")
                seen[digest] = content
    except ReleaseCliError:
        raise
    except (OSError, tarfile.TarError):
        raise ReleaseCliError("release_artifact_bundle_invalid") from None
    if set(seen) != set(expected):
        raise ReleaseCliError("release_artifact_bundle_incomplete")
    return seen


def promote_evidence(args: argparse.Namespace) -> dict[str, Any]:
    """Promote verified evidence into an append-only, code-owned target root.

    Production binds ``MINDATLAS_RELEASE_PROMOTION_ROOT`` to the reviewed
    release-evidence bucket mount. The CLI accepts only the safe target alias;
    endpoint, bucket, and object prefixes are not caller inputs.
    """

    evidence_path = _require_regular_file(args.evidence, code="release_evidence_missing")
    bundle_path = _require_regular_file(args.artifact_bundle, code="release_artifact_bundle_missing")
    trust_path = _require_regular_file(args.trust_set, code="release_trust_set_missing")
    target_alias = _require_safe_alias(args.target_alias)
    raw_fd = getattr(args, "destination_credential_fd", None)
    if raw_fd is None:
        raw_fd = getattr(args, "credential_fd", None)
    if raw_fd is None:
        raw_fd = os.environ.get("MINDATLAS_RELEASE_PROMOTION_CREDENTIAL_FD", "")
    try:
        credential_fd = int(raw_fd)
    except (TypeError, ValueError):
        raise ReleaseCliError("release_promotion_credential_fd_unavailable") from None
    _consume_descriptor(credential_fd, code="release_promotion_credential_fd_unavailable")
    root_value = os.environ.get("MINDATLAS_RELEASE_PROMOTION_ROOT", "")
    root = Path(root_value) if root_value else None
    if root is None or not root.is_absolute() or root.is_symlink():
        raise ReleaseCliError("release_promotion_target_unavailable")
    if root.exists() and not root.is_dir():
        raise ReleaseCliError("release_promotion_target_unavailable")
    try:
        from app.release.evidence import verify_evidence_object
        from app.release.trust import attestation_object_digest, load_trust_set
        from scripts.verify_release_attestation import verify

        summary, exit_code = verify(evidence_path, bundle_path, trust_path)
        payload = _json_file(evidence_path)
        manifest, attestation = verify_evidence_object(payload, load_trust_set(trust_path))
    except Exception:
        raise ReleaseCliError("release_evidence_verification_failed") from None
    if exit_code != 0 or summary.get("failedAssertions", 1) != 0:
        raise ReleaseCliError("release_evidence_assertions_failed")
    requested_kind = getattr(args, "kind", None)
    if requested_kind is not None and requested_kind != manifest.evidence_kind:
        raise ReleaseCliError("release_evidence_kind_mismatch")
    artifact_contents = _read_bundle_artifacts(bundle_path, manifest)
    target_root = root / target_alias
    if target_root.exists() and target_root.is_symlink():
        raise ReleaseCliError("release_promotion_target_unavailable")
    try:
        target_root.mkdir(parents=True, exist_ok=True)
    except OSError:
        raise ReleaseCliError("release_promotion_target_unavailable") from None
    artifact_idempotent = True
    for digest, content in sorted(artifact_contents.items()):
        replayed = _conditional_create(
            target_root / "release-evidence-artifacts" / "v1" / digest[:2] / digest,
            content,
            root=target_root,
        )
        artifact_idempotent = artifact_idempotent and replayed
    evidence_content = evidence_path.read_bytes()
    replayed_evidence = _conditional_create(
        target_root
        / "release-evidence"
        / "v1"
        / manifest.evidence_kind
        / manifest.manifest_digest[:2]
        / f"{manifest.manifest_digest}.json",
        evidence_content,
        root=target_root,
    )
    return {
        "state": "evidence-promoted",
        "targetAlias": target_alias,
        "evidenceKind": manifest.evidence_kind,
        "manifestDigest": manifest.manifest_digest,
        "attestationDigest": attestation_object_digest(attestation),
        "artifactAggregateDigest": manifest.artifact_aggregate_digest,
        "artifactCount": len(artifact_contents),
        "idempotent": artifact_idempotent and replayed_evidence,
    }


_CLONE_NEGATIVE_CASES = frozenset(
    {
        "fileOutputRefused",
        "remoteDestinationRefused",
        "nonemptyDestinationRefused",
        "launchedSourceRefused",
        "legacyRevisionRefused",
    }
)


def _validate_clone_result(path: Path, *, target_digest: str, bundle_digest: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ReleaseCliError("release_clone_result_missing")
    try:
        raw = _json_file(path)
    except ReleaseCliError:
        raise ReleaseCliError("release_clone_result_invalid") from None
    if not isinstance(raw, dict):
        raise ReleaseCliError("release_clone_result_invalid")
    required = {
        "schemaVersion",
        "state",
        "sourceDeploymentClass",
        "sourceSchemaRevision",
        "sourceControlRevision",
        "sourceLaunched",
        "qualificationTargetDigest",
        "ociBundleDigest",
        "destroyed",
        "cases",
    }
    if set(raw) != required:
        raise ReleaseCliError("release_clone_result_invalid")
    if (
        raw.get("schemaVersion") != 1
        or raw.get("state") != "negative-acceptance-passed"
        or raw.get("sourceDeploymentClass") != "production"
        or raw.get("sourceSchemaRevision") != "pre_ga_v1_0002"
        or not isinstance(raw.get("sourceControlRevision"), int)
        or isinstance(raw.get("sourceControlRevision"), bool)
        or raw.get("sourceControlRevision") != 0
        or raw.get("sourceLaunched") is not False
        or raw.get("qualificationTargetDigest") != target_digest
        or raw.get("ociBundleDigest") != bundle_digest
        or raw.get("destroyed") is not True
    ):
        raise ReleaseCliError("release_clone_result_invalid")
    cases = raw.get("cases")
    if not isinstance(cases, dict) or set(cases) != _CLONE_NEGATIVE_CASES or any(
        value is not True for value in cases.values()
    ):
        raise ReleaseCliError("release_clone_negative_matrix_incomplete")
    return raw


def run_production_clone(args: argparse.Namespace) -> dict[str, Any]:
    """Run the destructive-looking clone harness only through its protected boundary.

    The host CLI freezes all public inputs and verifies the source/evidence
    material before delegating database access to a separately installed
    executor. The executor receives the source URL only through an inherited
    descriptor and must return a fixed, safe result after destroying its
    disposable destination. It never receives a destination URL or filename.
    """

    run_dir = getattr(args, "run_dir", None)
    if (
        not isinstance(run_dir, Path)
        or not run_dir.is_absolute()
        or run_dir.exists()
        or run_dir.is_symlink()
        or not run_dir.parent.is_dir()
    ):
        raise ReleaseCliError("release_clone_run_dir_must_not_exist")
    source_fd = getattr(args, "source_url_fd", None)
    _require_fd(source_fd, code="release_source_url_fd_unavailable")
    target_path = _require_regular_file(
        getattr(args, "qualification_target", None),
        code="release_qualification_target_missing",
    )
    automation_path = _require_regular_file(
        getattr(args, "automation_evidence", None),
        code="release_automation_evidence_missing",
    )
    rehearsal_path = _require_regular_file(
        getattr(args, "rehearsal_evidence", None),
        code="release_rehearsal_evidence_missing",
    )
    bundle_path = _require_regular_file(
        getattr(args, "oci_bundle", None),
        code="release_oci_bundle_missing",
    )
    if any(path.is_symlink() for path in (target_path, automation_path, rehearsal_path, bundle_path)):
        raise ReleaseCliError("release_clone_input_must_be_regular_file")
    trust_path = getattr(args, "trust_set", None)
    if trust_path is None:
        configured = os.environ.get("MINDATLAS_RELEASE_TRUST_SET_FILE", "")
        trust_path = Path(configured) if configured else None
    trust_path = _require_regular_file(trust_path, code="release_trust_set_missing")
    try:
        target = verify_target(target_path)
        target_digest = target["targetDigest"]
        _verified_manifest(automation_path, trust_path)
        _verified_manifest(rehearsal_path, trust_path)
        compare_evidence(automation_path, rehearsal_path, trust_path)
        archive = _validate_archive_bundle(bundle_path)
        bundle_digest = archive["bundleDigest"]
    except ReleaseCliError:
        raise
    except Exception:
        raise ReleaseCliError("release_clone_input_verification_failed") from None

    executor_value = os.environ.get("MINDATLAS_RELEASE_CLONE_EXECUTOR", "")
    executor = Path(executor_value) if executor_value else None
    if (
        executor is None
        or not executor.is_absolute()
        or executor.is_symlink()
        or not executor.is_file()
        or not os.access(executor, os.X_OK)
    ):
        raise ReleaseCliError("release_clone_requires_protected_runner")
    try:
        if executor.resolve() == Path(__file__).resolve():
            raise ReleaseCliError("release_clone_protected_runner_self_reference")
    except OSError:
        raise ReleaseCliError("release_clone_requires_protected_runner") from None

    child_env = dict(os.environ)
    for name in tuple(child_env):
        upper = name.upper()
        if name.endswith("_FD") or any(
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
    child_env.update(
        {
            "MINDATLAS_RELEASE_CLONE_RUN_DIR": str(run_dir),
            "MINDATLAS_RELEASE_CLONE_TARGET_FILE": str(target_path),
            "MINDATLAS_RELEASE_CLONE_AUTOMATION_EVIDENCE": str(automation_path),
            "MINDATLAS_RELEASE_CLONE_REHEARSAL_EVIDENCE": str(rehearsal_path),
            "MINDATLAS_RELEASE_CLONE_OCI_BUNDLE": str(bundle_path),
            "MINDATLAS_RELEASE_CLONE_TRUST_SET": str(trust_path),
            "MINDATLAS_RELEASE_CLONE_TARGET_DIGEST": target_digest,
            "MINDATLAS_RELEASE_CLONE_OCI_BUNDLE_DIGEST": bundle_digest,
        }
    )
    command = [
        str(executor),
        "--source-database-url-fd",
        str(source_fd),
        "--run-dir",
        str(run_dir),
        "--qualification-target",
        str(target_path),
        "--automation-evidence",
        str(automation_path),
        "--rehearsal-evidence",
        str(rehearsal_path),
        "--oci-bundle",
        str(bundle_path),
        "--trust-set",
        str(trust_path),
    ]
    try:
        result = subprocess.run(
            command,
            cwd=str(BACKEND_ROOT.parent),
            env=child_env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=2 * 60 * 60,
            check=False,
            pass_fds=(source_fd,),
        )
    except (OSError, subprocess.TimeoutExpired):
        raise ReleaseCliError("release_clone_protected_runner_failed") from None
    if result.returncode != 0:
        raise ReleaseCliError("release_clone_protected_runner_failed")
    summary = _validate_clone_result(
        run_dir / "clone-negative-acceptance.json",
        target_digest=target_digest,
        bundle_digest=bundle_digest,
    )
    return {
        "state": summary["state"],
        "qualificationTargetDigest": target_digest,
        "ociBundleDigest": bundle_digest,
        "destroyed": True,
        "cases": {name: True for name in sorted(_CLONE_NEGATIVE_CASES)},
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
    _require_regular_file(evidence_path, code="release_evidence_missing")
    _require_regular_file(artifact_bundle, code="release_artifact_bundle_missing")
    _require_regular_file(trust_set, code="release_trust_set_missing")
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


def verify_launch_summary(summary_path: Path) -> dict[str, Any]:
    """Validate the repository-safe launch projection without asserting live state."""

    summary_path = _require_regular_file(summary_path, code="release_launch_summary_missing")
    from app.release.contracts import SafeReleaseEvidenceSummaryV1

    try:
        summary = SafeReleaseEvidenceSummaryV1.model_validate(_json_file(summary_path))
    except (ReleaseCliError, ValueError):
        raise ReleaseCliError("release_launch_summary_invalid") from None
    if (
        summary.assertion_failed != 0
        or summary.offline_verification != "passed"
        or summary.target_container_verification != "passed"
        or summary.soak_claimed is not False
    ):
        raise ReleaseCliError("release_launch_summary_invalid")
    return {
        "state": "launch-summary-verified",
        "evidenceKind": summary.evidence_kind,
        "releaseSourceRevision": summary.release_source_revision,
        "qualificationTargetDigest": summary.qualification_target_digest,
        "manifestDigest": summary.manifest_digest,
        "attestationDigest": summary.attestation_digest,
        "assertionPassed": summary.assertion_passed,
    }


def _validate_live_launch_result(path: Path, *, target_digest: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ReleaseCliError("release_launch_live_result_missing")
    try:
        raw = _json_file(path)
    except ReleaseCliError:
        raise ReleaseCliError("release_launch_live_result_invalid") from None
    if not isinstance(raw, dict):
        raise ReleaseCliError("release_launch_live_result_invalid")
    required = {
        "schemaVersion",
        "state",
        "qualificationTargetDigest",
        "launched",
        "ready",
        "controlRevision",
        "activeRunCount",
        "unresolvedCallCount",
        "workerCount",
    }
    if set(raw) != required or raw.get("schemaVersion") != 1 or raw.get("state") != "launch-verified":
        raise ReleaseCliError("release_launch_live_result_invalid")
    if (
        raw.get("qualificationTargetDigest") != target_digest
        or raw.get("launched") is not True
        or raw.get("ready") is not True
        or not isinstance(raw.get("controlRevision"), int)
        or isinstance(raw.get("controlRevision"), bool)
        or raw.get("controlRevision") < 1
        or raw.get("activeRunCount") != 0
        or raw.get("unresolvedCallCount") != 0
        or not isinstance(raw.get("workerCount"), int)
        or isinstance(raw.get("workerCount"), bool)
        or raw.get("workerCount") < 1
    ):
        raise ReleaseCliError("release_launch_live_result_invalid")
    return raw


def verify_launch(args: argparse.Namespace) -> dict[str, Any]:
    summary = verify_launch_summary(args.summary)
    target_digest = summary["qualificationTargetDigest"]
    if args.target is not None:
        target = verify_target(_require_regular_file(args.target, code="release_qualification_target_missing"))
        if target["targetDigest"] != target_digest:
            raise ReleaseCliError("release_launch_target_mismatch")
    if args.candidate is not None:
        candidate_path = _require_regular_file(args.candidate, code="release_launch_candidate_missing")
        candidate = _json_file(candidate_path)
        if not isinstance(candidate, dict):
            raise ReleaseCliError("release_launch_candidate_invalid")
        candidate_digest = candidate.get("qualificationTargetDigest") or candidate.get(
            "qualification_target_digest"
        )
        if candidate_digest != target_digest:
            raise ReleaseCliError("release_launch_candidate_mismatch")
    if args.base_url is None:
        return summary
    parsed = urlparse(args.base_url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ReleaseCliError("release_target_url_invalid")
    verifier_value = os.environ.get("MINDATLAS_RELEASE_LAUNCH_VERIFIER", "")
    verifier = Path(verifier_value) if verifier_value else None
    if (
        verifier is None
        or not verifier.is_absolute()
        or verifier.is_symlink()
        or not verifier.is_file()
        or not os.access(verifier, os.X_OK)
    ):
        raise ReleaseCliError("release_launch_requires_protected_verifier")
    try:
        if verifier.resolve() == Path(__file__).resolve():
            raise ReleaseCliError("release_launch_protected_verifier_self_reference")
    except OSError:
        raise ReleaseCliError("release_launch_requires_protected_verifier") from None
    with tempfile.TemporaryDirectory(prefix="mindatlas-launch-verify-") as raw_tmp:
        result_path = Path(raw_tmp) / "launch-verification.json"
        child_env = dict(os.environ)
        for name in tuple(child_env):
            upper = name.upper()
            if name.endswith("_FD") or any(
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
        child_env.update(
            {
                "MINDATLAS_RELEASE_LAUNCH_SUMMARY": str(args.summary),
                "MINDATLAS_RELEASE_LAUNCH_TARGET_DIGEST": target_digest,
                "MINDATLAS_RELEASE_LAUNCH_RESULT": str(result_path),
            }
        )
        command = [
            str(verifier),
            "--base-url",
            args.base_url,
            "--summary",
            str(args.summary),
        ]
        if args.target is not None:
            command.extend(("--target", str(args.target)))
        if args.candidate is not None:
            command.extend(("--candidate", str(args.candidate)))
        try:
            result = subprocess.run(
                command,
                cwd=str(BACKEND_ROOT.parent),
                env=child_env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30 * 60,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise ReleaseCliError("release_launch_protected_verifier_failed") from None
        if result.returncode != 0:
            raise ReleaseCliError("release_launch_protected_verifier_failed")
        live = _validate_live_launch_result(result_path, target_digest=target_digest)
    return {
        **summary,
        "state": "launch-verified",
        "controlRevision": live["controlRevision"],
        "workerCount": live["workerCount"],
    }


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
    prepare.add_argument("--deployment-identity", type=Path)
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
    build.add_argument("--signing-key-fd", type=int)
    build.add_argument("--deployment-key-id")
    build.add_argument("--no-build", action="store_true")
    artifact_verify = artifact_commands.add_parser("verify")
    artifact_verify.add_argument("--deployment-identity", type=Path, required=True)
    artifact_verify.add_argument("--oci-bundle", type=Path, required=True)
    artifact_verify.add_argument("--run-dir", type=Path)
    artifact_verify.add_argument("--trust-set", type=Path)

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
    clone_command.add_argument("--trust-set", type=Path)

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
        return promote_evidence(args)
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
            return build_artifact(args)
        return verify_artifact(args)
    if args.command == "launch":
        return verify_launch(args)
    if args.command == "production-clone":
        return run_production_clone(args)
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
