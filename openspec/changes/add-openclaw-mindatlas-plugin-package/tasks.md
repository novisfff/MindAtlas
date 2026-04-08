## 1. Plugin Package
- [x] 1.1 Add the standalone `integrations/openclaw-mindatlas` package with plugin manifest, package metadata, TypeScript config, runtime source files, and bundled skill assets.
- [x] 1.2 Implement capability discovery, tool registration, execute forwarding, error mapping, TTL refresh, and structure-drift reload warnings.
- [x] 1.3 Add plugin-local tests covering config parsing, catalog discovery, execution forwarding, and reload-required behavior.

## 2. Docs And Product Copy
- [x] 2.1 Add a plugin README that documents installation, config, runtime behavior, and development checks.
- [x] 2.2 Update MindAtlas OpenClaw docs and settings-page integration guidance so they point to the real plugin package and install command.

## 3. Validation
- [x] 3.1 Run `openspec validate add-openclaw-mindatlas-plugin-package --strict --no-interactive`.
- [x] 3.2 Run plugin package build and tests.
- [x] 3.3 Re-run affected frontend verification after updating the settings guide copy.
