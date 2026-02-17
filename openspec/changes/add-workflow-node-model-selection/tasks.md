## 1. OpenSpec
- [x] 1.1 创建 proposal/tasks/spec delta 并通过 `openspec validate --strict --no-interactive`

## 2. Backend Validation
- [x] 2.1 在 workflow validator 增加 `llm` / `parameter_extractor` 的 `modelSource/modelId` 规则
- [x] 2.2 在 assistant config service 增加保存期模型依赖校验（存在性 + 类型）
- [x] 2.3 在 validate-workflow 路由接入依赖校验，提前返回错误

## 3. Backend Runtime
- [x] 3.1 在 AI runtime 增加 `model_id` 解析函数
- [x] 3.2 在 LangGraphEngine 增加节点级模型解析与缓存
- [x] 3.3 `llm` / `parameter_extractor` 节点按配置路由到对应模型并保留默认回退
- [x] 3.4 自定义模型失效时运行期显式报错

## 4. Frontend
- [x] 4.1 扩展 workflow 节点 config 类型（LLM + ParameterExtractor）
- [x] 4.2 属性面板接入 llm 模型列表并透传到节点设置组件
- [x] 4.3 在 LLM 与参数提取节点设置中增加“默认/指定模型”交互
- [x] 4.4 新建节点默认写入 `modelSource: 'default'`
- [x] 4.5 补充中英文 i18n 文案

## 5. Tests
- [x] 5.1 更新后端 validator 测试（字段合法性/冲突）
- [x] 5.2 更新 service 测试（保存期模型存在性和类型）
- [x] 5.3 更新 runtime 测试（节点路由、缓存复用、失效报错）
