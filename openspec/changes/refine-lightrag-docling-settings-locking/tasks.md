## 1. Implementation
- [x] 1.1 Add the OpenSpec delta for LightRAG and Docling startup-owned and initialization-locked settings behavior
- [x] 1.2 Tighten backend runtime config updates so initialized systems cannot mutate deployment-owned LightRAG or Docling startup fields
- [x] 1.3 Simplify the LightRAG settings page to show startup state, hide Neo4j and graph-storage controls, and lock embedding host after initialization
- [x] 1.4 Simplify the Docling settings page to show worker startup state and only allow OCR and image-description editing when the worker is running
- [x] 1.5 Update shared frontend validation and localized copy to match the new locking and unavailable-state behavior

## 2. Validation
- [x] 2.1 Run `openspec validate refine-lightrag-docling-settings-locking --strict --no-interactive`
- [x] 2.2 Run targeted backend tests for the new locking rules
- [x] 2.3 Run the frontend build to confirm the simplified settings pages compile
