## 1. Implementation
- [x] 1.1 Add backend `http_request` execution module and node builder
- [x] 1.2 Wire `http_request` into DAG assembler, engine wrappers, and container runtime dispatch
- [x] 1.3 Add backend validation rules and template reference scanning support
- [x] 1.4 Update backend NodeType/schema metadata and snapshot input resolver
- [x] 1.5 Add backend config defaults for timeout/retry/response size limits
- [x] 1.6 Add frontend NodeType/config typings and default node factory config
- [x] 1.7 Add frontend node catalog/canvas/layout/header/quick-add support
- [x] 1.8 Add frontend `HttpRequestNodeSettings` and PropertyPanel routing
- [x] 1.9 Add frontend variable reference inference and reference transform rewrites
- [x] 1.10 Add i18n labels and editor strings for HTTP node

## 2. Verification
- [x] 2.1 Add validator tests for valid/invalid `http_request` configs and container support
- [x] 2.2 Add runtime tests for 2xx/4xx/5xx retry/transport failure and SSL branch
- [x] 2.3 Run `openspec validate add-workflow-http-request-node --strict --no-interactive`
- [x] 2.4 Run targeted backend tests and frontend build
