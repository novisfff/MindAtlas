## 1. Implementation
- [x] 1.1 Add the OpenSpec delta for the new initialization intro step
- [x] 1.2 Insert the intro step into the initialization wizard flow and shift later steps
- [x] 1.3 Render localized introduction content plus LightRAG and Docling status cards from existing runtime config data
- [x] 1.4 Update persisted step migration and validation copy for the six-step flow

## 2. Validation
- [x] 2.1 Run `openspec validate add-initialization-intro-and-plugin-status-step --strict --no-interactive`
- [x] 2.2 Run the frontend build to confirm the new step and i18n changes compile
