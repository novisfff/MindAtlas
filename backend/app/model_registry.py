from __future__ import annotations


def load_all_live_models() -> None:
    """Import every live ORM module so it registers with ``Base.metadata``."""
    import app.ai_provider.models
    import app.ai_registry.models
    import app.assistant.models
    import app.assistant.capability_calls.models
    import app.assistant.durable.models
    import app.assistant.evaluation.models
    import app.assistant.runtime.models
    import app.assistant.skills.models
    import app.assistant_config.models
    import app.attachment.models
    import app.entry.models
    import app.entry_type.models
    import app.lightrag.models
    import app.openclaw_integration.models
    import app.operator_auth.models
    import app.pre_ga_launch.models
    import app.relation.models
    import app.report.models
    import app.system_settings.models
    import app.tag.models

    modules = (
        app.ai_provider.models,
        app.ai_registry.models,
        app.assistant.models,
        app.assistant.capability_calls.models,
        app.assistant.durable.models,
        app.assistant.evaluation.models,
        app.assistant.runtime.models,
        app.assistant.skills.models,
        app.assistant_config.models,
        app.attachment.models,
        app.entry.models,
        app.entry_type.models,
        app.lightrag.models,
        app.openclaw_integration.models,
        app.operator_auth.models,
        app.pre_ga_launch.models,
        app.relation.models,
        app.report.models,
        app.system_settings.models,
        app.tag.models,
    )
    if any(module is None for module in modules):  # pragma: no cover
        raise RuntimeError("live model import failed")
