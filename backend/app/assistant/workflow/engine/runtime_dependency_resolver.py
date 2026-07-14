"""Generic exact runtime dependency resolver Protocol for Workflow engine reuse.

The Workflow engine module owns only this Protocol/scope and never imports
``app.assistant.capabilities``. Capability execution implements the Protocol in
``capabilities/execution_closure.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

# Plan 02 platform execution ceiling (independent from Plan 01 closure depth 16).
# Root starts at 0; each nested workflow_call increments once; depth 5 is denied.
MAX_CAPABILITY_NESTING_DEPTH = 4


class ExactRuntimeDependencyResolver(Protocol):
    def require_tool(
        self,
        *,
        source_locator: str,
        tool_name: str,
    ) -> object: ...

    def require_workflow_version(
        self,
        *,
        source_locator: str,
        workflow_id: UUID,
        version_id: UUID,
    ) -> object: ...

    def require_model(
        self,
        *,
        source_locator: str,
        requested_model_id: UUID | None,
    ) -> object: ...


@dataclass(frozen=True)
class WorkflowEngineExecutionScope:
    dependency_resolver: ExactRuntimeDependencyResolver
    binding_contract_digest: str
    dependency_closure_digest: str
    nesting_depth: int
    safe_diagnostics: bool = True
    allow_ambient_memory: bool = False
    allow_global_graph_cache: bool = False


__all__ = [
    "MAX_CAPABILITY_NESTING_DEPTH",
    "ExactRuntimeDependencyResolver",
    "WorkflowEngineExecutionScope",
]
