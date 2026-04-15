## Context

The current OpenClaw MindAtlas plugin intentionally keeps dedicated `mindatlas_*` tools stable for the life of a Gateway/session. That works well for dedicated built-in capabilities, but it leaves a gap for newly exposed custom capabilities: the plugin can discover them on refresh, yet the current session cannot call them through a dedicated tool and the shipped skills do not clearly instruct OpenClaw to discover or use them.

## Goals

- Preserve the dedicated-tool surface and its current routing quality
- Add a stable dispatcher path that can discover and run newly exposed capabilities without requiring dedicated late-registration
- Teach the shipped skills to use custom capability surfaces, not just system defaults

## Non-Goals

- Change the MindAtlas backend capability catalog or execution APIs
- Remove dedicated `mindatlas_*` tools
- Hot-register dedicated tools into an already-running Gateway/session

## Decisions

### 1. Add stable dispatcher tools in the plugin

The plugin will always register `mindatlas_list_capabilities` and `mindatlas_run_capability` whenever plugin config is valid. These tools are plugin-local and do not depend on a catalog item existing for themselves.

### 2. Keep dedicated tools as the preferred route

Dedicated tools remain the first choice because they provide stronger tool-level routing hints and input schemas. Dispatcher is a discovery and fallback path, not a replacement for dedicated routing.

### 3. Refresh only on dispatcher calls

Dedicated tools keep the current TTL-based snapshot behavior. Dispatcher tools perform an extra manual refresh before listing or executing so newly exposed custom capabilities can be discovered without waiting for a dedicated tool to become session-visible.

## Risks / Trade-offs

- Dispatcher adds another execution path, so skills must be explicit about when to use it
- Startup catalog failures will still prevent dedicated tool registration, but dispatcher tools remain available for recovery once connectivity returns
