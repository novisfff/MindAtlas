"""Deterministic Plan 04 dataset fixture import (Plan 09 Task 3).

Preserves the checked-in ``main_agent_eval/read_only_v1.jsonl`` fixture with
fixed dataset/version/case digests and idempotent re-import.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence
from uuid import UUID, uuid5, NAMESPACE_URL

from sqlalchemy.orm import Session

from app.assistant.domain.digests import sha256_canonical_json
from app.assistant.main_agent.evaluation import (
    EvalCase,
    dataset_digest as plan04_dataset_digest,
    load_dataset,
)
from app.assistant.evaluation.repository import EvaluationRepository

# Stable deterministic IDs for the Plan 04 system dataset.
PLAN04_DATASET_KEY = "main-agent-read-only-v1"
PLAN04_DATASET_DISPLAY_NAME = "Main Agent Read-Only Evaluation v1"
PLAN04_DATASET_DESCRIPTION = (
    "Plan 04 fixed offline evaluation dataset for Main Agent read-only golden path."
)
PLAN04_FIXTURE_REVISION = "plan04-read-only-v1"
PLAN04_VERSION_NAME = "v1"
PLAN04_NAMESPACE = uuid5(NAMESPACE_URL, "mindatlas:plan09:eval-dataset:main-agent-read-only-v1")

DEFAULT_FIXTURE_PATH = (
    Path(__file__).resolve().parents[3]
    / "tests"
    / "fixtures"
    / "main_agent_eval"
    / "read_only_v1.jsonl"
)


def plan04_dataset_id() -> UUID:
    return uuid5(PLAN04_NAMESPACE, "dataset")


def plan04_version_id() -> UUID:
    return uuid5(PLAN04_NAMESPACE, "version:v1")


def plan04_case_id(case_key: str) -> UUID:
    return uuid5(PLAN04_NAMESPACE, f"case:{case_key}")


def case_digest(case: EvalCase) -> str:
    return sha256_canonical_json(case.to_dict())


def version_content_digest(cases: Sequence[EvalCase]) -> str:
    """Content digest for the immutable Dataset Version (matches Plan 04)."""
    return plan04_dataset_digest(cases)


@dataclass(frozen=True, slots=True)
class DatasetImportResult:
    dataset_id: UUID
    version_id: UUID
    case_count: int
    content_digest: str
    created: bool
    case_ids: tuple[UUID, ...]


def fixture_path(path: Path | None = None) -> Path:
    return path or DEFAULT_FIXTURE_PATH


def load_plan04_cases(path: Path | None = None) -> list[EvalCase]:
    return load_dataset(fixture_path(path))


def provider_script_key_for_case(case: EvalCase) -> str:
    """Stable Provider script key for a Plan 04 case (separate from assertions)."""
    return f"plan04:{case.execution_kind}:{case.case_id}"


def case_to_snapshot_row(case: EvalCase, *, ordinal: int) -> dict[str, Any]:
    # Provider script refs are named independently of expected Skill/Capability
    # assertions so scripted Provider execution can resolve fixtures without
    # reading assertion fields.
    return {
        "case_key": case.case_id,
        "ordinal": ordinal,
        "locale": case.locale,
        "input_messages": [{"role": "user", "content": case.prompt}],
        "fixture_refs": [
            {
                "kind": "provider_script",
                "script_key": provider_script_key_for_case(case),
                "revision": PLAN04_FIXTURE_REVISION,
            }
        ],
        "expected_mode": case.execution_kind,
        "acceptable_skill_keys": list(case.acceptable_skills),
        "forbidden_skill_keys": list(case.forbidden_skills),
        "acceptable_capability_paths": [list(path) for path in case.acceptable_capability_paths],
        "forbidden_side_effect_classes": [],
        "expect_completion": bool(case.expect_completion),
        "tags": [case.execution_kind],
        "notes": case.notes,
        "assertion_json": {
            "direct_answer_allowed": bool(case.direct_answer_allowed),
            "execution_kind": case.execution_kind,
        },
        "ceilings_json": {},
        "case_digest": case_digest(case),
    }


def import_plan04_dataset(
    session: Session,
    *,
    path: Path | None = None,
    actor: str = "system:plan04-fixture",
    repository: EvaluationRepository | None = None,
) -> DatasetImportResult:
    """Idempotently import the Plan 04 read-only dataset.

    Re-running with the same fixture returns the existing version/case IDs and
    digests without creating duplicates.
    """
    repo = repository or EvaluationRepository(session)
    cases = load_plan04_cases(path)
    content_digest = version_content_digest(cases)
    dataset_id = plan04_dataset_id()
    version_id = plan04_version_id()

    existing = repo.get_dataset(dataset_id)
    if existing is not None and existing.current_version_id is not None:
        version = repo.get_dataset_version(existing.current_version_id)
        if version is not None and version.content_digest == content_digest:
            case_rows = repo.list_cases(version.id)
            return DatasetImportResult(
                dataset_id=existing.id,
                version_id=version.id,
                case_count=len(case_rows),
                content_digest=content_digest,
                created=False,
                case_ids=tuple(row.id for row in case_rows),
            )

    snapshot_rows = [
        case_to_snapshot_row(case, ordinal=index)
        for index, case in enumerate(cases)
    ]
    # Fixed UUIDs for deterministic re-import.
    for row, case in zip(snapshot_rows, cases, strict=True):
        row["id"] = str(plan04_case_id(case.case_id))

    if existing is None:
        dataset = repo.create_dataset(
            dataset_id=dataset_id,
            stable_key=PLAN04_DATASET_KEY,
            display_name=PLAN04_DATASET_DISPLAY_NAME,
            description=PLAN04_DATASET_DESCRIPTION,
            ownership="system",
            actor=actor,
        )
    else:
        dataset = existing

    draft = repo.get_or_create_draft(
        dataset_id=dataset.id,
        cases_snapshot=snapshot_rows,
        actor=actor,
    )
    published = repo.publish_dataset_version(
        dataset_id=dataset.id,
        expected_aggregate_revision=int(dataset.aggregate_revision),
        expected_draft_revision=int(draft.draft_revision),
        version_id=version_id,
        version_name=PLAN04_VERSION_NAME,
        source_fixture_revision=PLAN04_FIXTURE_REVISION,
        actor=actor,
        fixed_case_ids=True,
        content_digest_override=content_digest,
    )
    return DatasetImportResult(
        dataset_id=dataset.id,
        version_id=published.version_id,
        case_count=published.case_count,
        content_digest=published.content_digest,
        created=True,
        case_ids=published.case_ids,
    )
