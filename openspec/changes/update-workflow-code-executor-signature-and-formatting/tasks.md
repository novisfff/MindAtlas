## 1. Backend
- [x] 1.1 Change code executor runtime invocation to dynamic parameter mapping from `inputBindings`.
- [x] 1.2 Enforce function signature parameter set equals `inputBindings` key set (save + compile + container body).
- [x] 1.3 Keep key validation (`[a-zA-Z_][a-zA-Z0-9_]*`) and value-string validation for input bindings.
- [x] 1.4 Update runtime node builder to resolve arbitrary input binding keys and pass `inputs` map.

## 2. Frontend
- [x] 2.1 Use one-line dynamic binding rows (add/remove/rename key + edit value).
- [x] 2.2 Update default Python template to the fixed signature version.
- [x] 2.3 Show only declared output fields in output list panel.
- [x] 2.4 Switch Python formatter to Ruff WASM and keep JS formatter on Prettier.
- [x] 2.5 Add Vite optimizeDeps exclusion for Ruff WASM package.
- [x] 2.6 Update i18n strings for editable default bindings and dynamic signature hint.

## 3. Tests
- [x] 3.1 Update validator tests to cover dynamic signature matching + custom keys + mismatch rejection.
- [x] 3.2 Update runtime tests to cover new runner contract.
- [x] 3.3 Update publish gate tests to block signature/binding mismatch and allow custom-key success.

## 4. Spec
- [x] 4.1 Add OpenSpec change files (`proposal`, `design`, `tasks`, `spec delta`).
- [x] 4.2 Validate with `openspec validate update-workflow-code-executor-signature-and-formatting --strict --no-interactive`.
