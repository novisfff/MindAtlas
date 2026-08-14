"""Credential-free, read-only target material for a rehearsal initializer.

The fixture is a transport object only.  It is deliberately not coupled to a
SQLAlchemy session, so loading or binding it cannot create a Profile, Model,
rollout, seed, or initialization marker.  The normal setup coordinator owns
that transaction in a real release profile.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any, Literal, Mapping, Protocol
from uuid import UUID

from app.assistant.domain.digests import sha256_canonical_json
from app.release.contracts import ReleaseContract, ReleaseQualificationTargetV1


FIXTURE_DOMAIN = "mindatlas:rehearsal-initialization-fixture:v1"
CONFIGURATION_DOMAIN = "mindatlas:rehearsal-initialization-configuration:v1"
_FORBIDDEN_KEY = re.compile(
    r"(?i)(password|passwd|secret|token|api[_-]?key|authorization|bearer|cookie|prompt|entry[_-]?body|artifact[_-]?body|provider[_-]?credential)"
)


class TargetFixtureError(ValueError):
    """Stable, safe failure from target-fixture validation."""

    def __init__(self, safe_code: str, message: str | None = None) -> None:
        self.safe_code = safe_code
        super().__init__(message or safe_code)


def _assert_safe_configuration(value: Any, *, path: str = "configuration") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str) or _FORBIDDEN_KEY.search(key):
                raise TargetFixtureError("target_fixture_sensitive_field")
            _assert_safe_configuration(item, path=f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _assert_safe_configuration(item, path=f"{path}[{index}]")
        return
    if isinstance(value, (str, int, float, bool)) or value is None:
        return
    raise TargetFixtureError("target_fixture_value_not_json")


def configuration_digest(configuration: Mapping[str, Any]) -> str:
    _assert_safe_configuration(configuration)
    return sha256_canonical_json(
        {"domain": CONFIGURATION_DOMAIN, "configuration": dict(configuration)}
    )


class RehearsalInitializationFixtureV1(ReleaseContract):
    schema_version: Literal[1] = 1
    target: ReleaseQualificationTargetV1
    provisioning: dict[str, Any]
    provisioning_digest: str
    fixture_digest: str

    @classmethod
    def build(
        cls,
        *,
        target: ReleaseQualificationTargetV1,
        provisioning: Mapping[str, Any],
    ) -> "RehearsalInitializationFixtureV1":
        if not isinstance(target, ReleaseQualificationTargetV1):
            raise TargetFixtureError("target_fixture_target_invalid")
        safe = deepcopy(dict(provisioning))
        digest = configuration_digest(safe)
        values: dict[str, Any] = {
            "target": target,
            "provisioning": safe,
            "provisioning_digest": digest,
        }
        values["fixture_digest"] = sha256_canonical_json(
            {
                "domain": FIXTURE_DOMAIN,
                "targetDigest": target.qualification_target_digest,
                "provisioningDigest": digest,
            }
        )
        return cls.model_validate(values)

    @classmethod
    def from_file(cls, path: Path) -> "RehearsalInitializationFixtureV1":
        path = Path(path)
        if not path.is_absolute() or path.is_symlink():
            raise TargetFixtureError("target_fixture_path_invalid")
        try:
            mode = path.stat().st_mode
            if not path.is_file() or mode & 0o077:
                raise TargetFixtureError("target_fixture_permissions_invalid")
            raw = json.loads(path.read_text(encoding="utf-8"))
            fixture = cls.model_validate(raw)
        except TargetFixtureError:
            raise
        except (OSError, UnicodeError, ValueError, TypeError):
            raise TargetFixtureError("target_fixture_invalid") from None
        return fixture

    def model_post_init(self, __context: Any) -> None:
        _assert_safe_configuration(self.provisioning)
        declared_target_digest = self.provisioning.get("targetDigest")
        if declared_target_digest is not None and declared_target_digest != self.target.qualification_target_digest:
            raise TargetFixtureError("target_fixture_target_digest_mismatch")
        if self.provisioning_digest != configuration_digest(self.provisioning):
            raise TargetFixtureError("target_fixture_provisioning_digest_mismatch")
        expected = sha256_canonical_json(
            {
                "domain": FIXTURE_DOMAIN,
                "targetDigest": self.target.qualification_target_digest,
                "provisioningDigest": self.provisioning_digest,
            }
        )
        if self.fixture_digest != expected:
            raise TargetFixtureError("target_fixture_digest_mismatch")


class RehearsalInitializationFixtureInitializer(Protocol):
    """Port implemented by the normal setup coordinator, not the fixture."""

    def initialize_from_fixture(
        self,
        *,
        target: ReleaseQualificationTargetV1,
        provisioning: Mapping[str, Any],
        fixture_digest: str,
        profile_run_id: UUID,
    ) -> Any: ...


@dataclass(frozen=True)
class RehearsalInitializationFixturePort:
    """Signed-run-bound read-only values exposed to the setup coordinator."""

    fixture: RehearsalInitializationFixtureV1
    profile_run_id: UUID

    @property
    def fixture_digest(self) -> str:
        return self.fixture.fixture_digest

    @property
    def target(self) -> ReleaseQualificationTargetV1:
        return self.fixture.target

    def provisioning(self) -> dict[str, Any]:
        return deepcopy(self.fixture.provisioning)

    def bind(self, profile_run_id: UUID) -> "RehearsalInitializationFixturePort":
        if not isinstance(profile_run_id, UUID):
            raise TargetFixtureError("target_fixture_profile_run_invalid")
        return RehearsalInitializationFixturePort(
            fixture=self.fixture,
            profile_run_id=profile_run_id,
        )

    def initialize(self, initializer: RehearsalInitializationFixtureInitializer) -> Any:
        if not hasattr(initializer, "initialize_from_fixture"):
            raise TargetFixtureError("target_fixture_initializer_invalid")
        return initializer.initialize_from_fixture(
            target=self.target,
            provisioning=self.provisioning(),
            fixture_digest=self.fixture_digest,
            profile_run_id=self.profile_run_id,
        )


def load_fixture_port(path: Path, *, profile_run_id: UUID) -> RehearsalInitializationFixturePort:
    return RehearsalInitializationFixturePort(
        fixture=RehearsalInitializationFixtureV1.from_file(path),
        profile_run_id=profile_run_id,
    )


__all__ = [
    "CONFIGURATION_DOMAIN",
    "FIXTURE_DOMAIN",
    "RehearsalInitializationFixtureInitializer",
    "RehearsalInitializationFixturePort",
    "RehearsalInitializationFixtureV1",
    "TargetFixtureError",
    "configuration_digest",
    "load_fixture_port",
]
