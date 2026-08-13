"""Execution-boundary qualification for the Provider create_entry declaration.

These tests intentionally invoke the declaration outside the capability
gateway.  A direct Python call must be incapable of acquiring a Session or
performing a legacy tool write.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests._bootstrap import bootstrap_backend_imports, reset_caches
from tests._db import make_session

bootstrap_backend_imports()
reset_caches()


def _declaration_function():
    from app.assistant.tools import create_entry

    return getattr(create_entry, "func", create_entry)


def _database_effect_snapshot(db):  # noqa: ANN001
    from app.assistant.capability_calls.models import AssistantCapabilityCall
    from app.entry.models import Entry

    return {
        "calls": db.query(AssistantCapabilityCall).count(),
        "entries": db.query(Entry).count(),
        "new": tuple(sorted(type(item).__name__ for item in db.new)),
    }


class _CommitForbiddenSession:
    def commit(self) -> None:
        raise AssertionError("the Provider declaration must not commit")


def test_gateway_required_decorated_create_entry_cannot_write_outside_gateway():
    from app.assistant.capability_calls.create_entry_declaration import (
        CapabilityGatewayRequired,
    )
    from app.assistant.tools._context import reset_current_db, set_current_db

    db = make_session()
    token = set_current_db(_CommitForbiddenSession())
    try:
        before = _database_effect_snapshot(db)
        with pytest.raises(CapabilityGatewayRequired) as exc:
            _declaration_function()(title="gateway boundary", content="must not write")
        assert exc.value.safe_code == "capability_gateway_required"
        assert _database_effect_snapshot(db) == before
    finally:
        reset_current_db(token)
        db.close()


def test_verified_gateway_invocation_returns_normalized_nonwriting_proposal():
    from app.assistant.capability_calls.create_entry_declaration import (
        _gateway_invocation_for_capability_adapter,
    )

    proposal = _declaration_function()(
        title="  Gateway title  ",
        content="  Gateway body  ",
        tags=[" alpha ", "", "beta"],
        _gateway_invocation=_gateway_invocation_for_capability_adapter(),
    )

    assert proposal.model_dump(mode="json") == {
        "title": "Gateway title",
        "summary": None,
        "content": "Gateway body",
        "type_code": None,
        "tags": ["alpha", "beta"],
        "time_mode": None,
        "time_at": None,
        "time_from": None,
        "time_to": None,
    }


def test_gateway_injected_marker_survives_tool_argument_validation():
    from app.assistant.capability_calls.create_entry_declaration import (
        _gateway_invocation_for_capability_adapter,
    )
    from app.assistant.tools import create_entry
    from app.assistant.workflow.engine.runtime_helpers import wrap_tool_with_db

    db = make_session()
    try:
        proposal = wrap_tool_with_db(create_entry, db.get_bind())(
            title="adapter title",
            content="adapter body",
            _gateway_invocation=_gateway_invocation_for_capability_adapter(),
        )
    finally:
        db.close()

    assert proposal.title == "adapter title"
    assert proposal.content == "adapter body"


def test_provider_json_cannot_forge_gateway_invocation_or_expose_it_in_schema():
    from app.assistant.capability_calls.create_entry_declaration import (
        CapabilityGatewayInvocation,
        CapabilityGatewayRequired,
        CreateEntryCapabilityInput,
    )
    from app.assistant.tools import create_entry

    schema = CreateEntryCapabilityInput.model_json_schema()
    assert "_gateway_invocation" not in schema["properties"]
    assert "_gateway_invocation" not in create_entry.args_schema.model_json_schema()[
        "properties"
    ]
    with pytest.raises(Exception):
        CreateEntryCapabilityInput.model_validate(
            {
                "title": "forged",
                "content": "payload",
                "_gateway_invocation": {"verified": True},
            }
        )
    from app.assistant.workflow.engine.runtime_helpers import coerce_tool_args

    with pytest.raises(Exception):
        coerce_tool_args(
            create_entry,
            {
                "title": "forged",
                "content": "payload",
                "_gateway_invocation": {"verified": True},
            },
        )
    with pytest.raises(CapabilityGatewayRequired):
        _declaration_function()(
            title="forged",
            content="payload",
            _gateway_invocation=CapabilityGatewayInvocation(object()),
        )


def test_local_adapter_has_no_provider_or_committing_service_dependency():
    from app.assistant.capability_calls import local_write

    tree = ast.parse(Path(local_write.__file__).read_text(encoding="utf-8"))
    imports = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }

    assert "app.assistant.tools.entry_tools" not in imports
    assert "create" not in calls
    assert "commit" not in calls
    assert "create_in_uow" in calls
