## 1. Implementation
- [x] 1.1 Add the OpenSpec delta for post-initialization LightRAG and Docling detail pages
- [x] 1.2 Add shared frontend runtime validation and lock helpers reused by initialization and settings pages
- [x] 1.3 Add backend LightRAG post-init readonly protection and explicit-clear update handling for runtime config payloads
- [x] 1.4 Add `/settings/lightrag` and `/settings/docling` pages plus routes, and keep `System Setup` as overview-only navigation
- [x] 1.5 Add direct Settings home entry cards for `LightRAG` and `Docling`, and update overview links and i18n

## 2. Validation
- [x] 2.1 Run `openspec validate add-post-init-lightrag-and-docling-settings-pages --strict --no-interactive`
- [x] 2.2 Run targeted backend tests for runtime config locking behavior
- [x] 2.3 Run the frontend build to confirm the new settings routes and shared validation compile
