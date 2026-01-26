## MODIFIED Requirements

### Requirement: Entry Relation Recommendation

系统 SHALL 提供基于 LightRAG 的 Entry 关系推荐功能，采用单阶段整合方案：

1. 使用向量检索获取候选 Entry 列表
2. 在单次 LLM 调用中完成候选选择和关系类型确定
3. 输出结构化 JSON: `{"relations": [{"targetEntryId": "UUID", "relationType": "CODE"}]}`

#### Scenario: 成功推荐关系

- **WHEN** 用户请求 Entry 关系推荐
- **AND** 源 Entry 存在且有内容
- **THEN** 系统返回推荐列表，每项包含 targetEntryId、relationType、score
- **AND** 所有 targetEntryId 必须存在于数据库中
- **AND** 所有 relationType 必须是启用的 RelationType.code

#### Scenario: ID 白名单校验

- **WHEN** LLM 输出包含不在候选白名单中的 targetEntryId
- **THEN** 系统过滤掉该无效项
- **AND** 仅返回通过校验的有效推荐

#### Scenario: JSON 解析失败降级

- **WHEN** LLM 输出无法解析为有效 JSON
- **THEN** 系统返回空推荐列表
- **AND** 记录警告日志

#### Scenario: 空内容 Entry

- **WHEN** 源 Entry 无标题、摘要、内容
- **THEN** 系统返回空推荐列表
