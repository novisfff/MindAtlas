## 1. Implementation
- [x] 1.1 Add stable dispatcher tools to the OpenClaw MindAtlas plugin runtime and register them independently from catalog-backed dedicated tools
- [x] 1.2 Extend runtime catalog snapshot handling so dispatcher tools can refresh and resolve capabilities by `capabilityKey`
- [x] 1.3 Keep existing dedicated tool reload / stale semantics intact while allowing dispatcher execution of newly exposed capabilities
- [x] 1.4 Add and rewrite shipped MindAtlas skills so overview can route to custom exposed capabilities through the dispatcher
- [x] 1.5 Update plugin README and operator guidance to explain dispatcher behavior and custom capability routing
- [x] 1.6 Add or update plugin and routing tests for dispatcher registration, listing, execution, and skill guidance
