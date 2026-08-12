"""Current native L2-memory ownership boundary."""

from __future__ import annotations

import inspect

from app.assistant.memory_service import AssistantMemoryService


def test_native_l2_service_uses_package_identity_without_legacy_runtime() -> None:
    source = inspect.getsource(AssistantMemoryService)

    assert "skill_package_id" in source
    assert "memory_namespace" in source
    assert "from app.assistant.migration" not in source
    assert AssistantMemoryService.normalize_l2_facts(
        [" first ", "first", "", "second"],
        max_items=10,
    ) == ["first", "second"]
