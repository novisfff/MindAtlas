## 1. Root Orchestration
- [x] 1.1 Introduce supervisor state model for root graph execution.
- [x] 1.2 Add LangGraph supervisor graph builder with explicit route/execute/fail transitions.
- [x] 1.3 Refactor `AssistantAgent.stream` to run through supervisor graph and queue-based streaming.

## 2. Routing
- [x] 2.1 Upgrade `SkillRouter.route` to structured `RouteDecision` output.
- [x] 2.2 Add JSON compatibility for legacy `skills[]` router output.
- [x] 2.3 Use deterministic fallback policy for invalid/empty router output.

## 3. Failure & Fallback Policy
- [x] 3.1 Remove direct-reply path when no routed skill is available.
- [x] 3.2 Remove service-level outer OpenAI fallback from assistant response generation.
- [x] 3.3 Keep skill failure behavior as hard failure (no automatic skill retry/rollback).

## 4. Observability
- [x] 4.1 Simplify router logs to suggested/selected/fallback fields.
- [x] 4.2 Add structured routing/skill failure logs without SSE protocol changes.

## 5. Validation
- [x] 5.1 Add unit tests for route decision parsing and default fallback behavior.
- [x] 5.2 Add unit tests for supervisor graph runtime semantics.
- [x] 5.3 Add unit test to assert no outer service fallback call.
- [x] 5.4 Run OpenSpec strict validation.
