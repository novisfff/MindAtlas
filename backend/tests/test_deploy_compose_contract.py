"""Contract tests for deployment identity propagation in Compose files."""

from __future__ import annotations

from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
BASE_COMPOSE = REPO_ROOT / "deploy" / "docker-compose.yml"
DEV_OVERRIDE = REPO_ROOT / "deploy" / "docker-compose.override.yml"
SMOKE_OVERLAY = REPO_ROOT / "deploy" / "compose.main-agent-smoke.yml"
SERVICES = (
    "db-migrate",
    "api",
    "lightrag-worker",
    "docling-worker",
    "assistant-worker",
)


class _ComposeLoader(yaml.SafeLoader):
    """Understand Docker Compose's merge-only ``!reset`` tag."""


_ComposeLoader.add_constructor(
    "!reset",
    lambda loader, node: loader.construct_sequence(node),
)


def _compose(path: Path) -> dict[str, object]:
    payload = yaml.load(path.read_text(encoding="utf-8"), Loader=_ComposeLoader)
    assert isinstance(payload, dict)
    return payload


def _environment(compose: dict[str, object], service: str) -> dict[str, object]:
    services = compose.get("services")
    assert isinstance(services, dict)
    definition = services.get(service)
    assert isinstance(definition, dict)
    environment = definition.get("environment")
    assert isinstance(environment, dict)
    return environment


def test_production_like_base_compose_fails_closed_without_development_default() -> (
    None
):
    source = BASE_COMPOSE.read_text(encoding="utf-8")

    assert ":-development" not in source
    assert "MINDATLAS_DEPLOYMENT_CLASS: development" not in source
    required_identity = (
        "MINDATLAS_DEPLOYMENT_CLASS: "
        "${MINDATLAS_DEPLOYMENT_CLASS:?Set MINDATLAS_DEPLOYMENT_CLASS "
        "to development, rehearsal, or production}"
    )
    assert source.count(required_identity) == 5


def test_local_override_is_the_only_development_identity_source() -> None:
    override = _compose(DEV_OVERRIDE)
    for service in SERVICES:
        assert (
            _environment(override, service)["MINDATLAS_DEPLOYMENT_CLASS"]
            == "development"
        )


def test_main_agent_smoke_explicitly_uses_rehearsal_identity() -> None:
    smoke = _compose(SMOKE_OVERLAY)
    for service in SERVICES:
        assert _environment(smoke, service)["MINDATLAS_DEPLOYMENT_CLASS"] == "rehearsal"
