# Change: Add OpenClaw MindAtlas Dispatcher Fallback And Dynamic Capability Routing

## Why
MindAtlas currently exposes dedicated `mindatlas_*` tools dynamically, but skill guidance still overfits the built-in system capability set. When administrators expose a new custom tool, workflow, or agent capability, OpenClaw can see the tool only after reload and new session, while the shipped skills still do not clearly teach the model to discover or use that custom surface.

## What Changes
- Add two stable plugin-side dispatcher tools: `mindatlas_list_capabilities` and `mindatlas_run_capability`
- Keep the existing dedicated `mindatlas_*` capability tools and their current stale / reload semantics
- Let dispatcher tools proactively refresh the remote capability catalog before listing or executing
- Expand shipped MindAtlas skills so they route across both built-in dedicated tools and administrator-exposed custom capabilities
- Add a dedicated dispatcher skill and update README/runtime docs accordingly

## Impact
- Affected specs: `external-agent-integration`
- Affected code: `integrations/openclaw-mindatlas`, shipped MindAtlas skills, plugin tests and routing tests
