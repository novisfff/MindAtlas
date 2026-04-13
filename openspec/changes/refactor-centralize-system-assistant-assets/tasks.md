## 1. Implementation
- [x] 1.1 Create the centralized `assistant/workflow/system_assets` registry and loader, and migrate 6 system workflow assets plus 1 system agent asset into it.
- [x] 1.2 Update `assistant.skill_catalog`, `assistant_config`, and `openclaw_integration` to reference system assets through `asset_key` and canonical names instead of preset paths or duplicated metadata.
- [x] 1.3 Remove legacy system asset manifest/preset loaders and old truth files outside the central asset directory.
- [x] 1.4 Add and update backend contract tests for central asset loading, system skill defaults, standalone targets, and system behavior defaults.
- [x] 1.5 Validate the change with backend tests and `openspec validate`.
