"""Workflow runtime package."""

from __future__ import annotations

from typing import Any

__all__ = ["LangGraphEngine", "build_workflow_dag_subgraph"]


def __getattr__(name: str) -> Any:
    if name in {"LangGraphEngine", "build_workflow_dag_subgraph"}:
        from app.assistant.workflow.engine import LangGraphEngine, build_workflow_dag_subgraph

        exports = {
            "LangGraphEngine": LangGraphEngine,
            "build_workflow_dag_subgraph": build_workflow_dag_subgraph,
        }
        return exports[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
