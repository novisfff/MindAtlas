## 1. OpenSpec
- [x] 1.1 新增 proposal/tasks/spec delta 并通过 `openspec validate --strict --no-interactive`

## 2. Backend Runtime
- [x] 2.1 改造 `parameter_extractor` 节点读取 `inputContent` 并渲染模板变量
- [x] 2.2 注入内置结构化提取提示词 + 可选 instruction + JSON 输出约束
- [x] 2.3 严格解析模型输出（非 JSON / 缺字段报错）
- [x] 2.4 输出统一为 `text(JSON字符串)` + `raw(对象)` + `json_fields(按 outputFields 过滤)`

## 3. Backend Validation
- [x] 3.1 `parameter_extractor.outputFields` 非空校验
- [x] 3.2 `inputContent` 类型与 `outputFields` 字段 schema 校验
- [x] 3.3 模板变量扫描纳入 `input_content/inputContent`

## 4. Frontend
- [x] 4.1 扩展 `ParameterExtractorNodeConfig`（`inputContent` 与完整 `outputFields` 结构）
- [x] 4.2 新建节点默认配置补齐：`modelSource/inputContent/outputFields`
- [x] 4.3 参数提取节点面板补齐 5 类配置（模型、输入内容、提取说明、输出参数配置、输出参数列表）
- [x] 4.4 变量引用与预览文案同步参数提取新字段
- [x] 4.5 补齐中英文文案

## 5. Tests
- [x] 5.1 `workflow_validator` 新增参数提取配置校验测试
- [x] 5.2 `langgraph_engine_streaming` 新增参数提取严格结构化执行测试
