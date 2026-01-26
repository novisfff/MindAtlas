# Change: 优化 Relation 推荐机制 - 单阶段整合 + 知识图谱元素

## Why

### Phase 1 (已完成)
当前 `recommend_entry_relations` 采用两阶段流程：Phase-1 向量召回 + Phase-2 LLM 关系类型推断。这导致：
1. 两次独立调用增加延迟
2. Phase-2 需要额外构建候选表格 prompt
3. 链路复杂度高，维护成本增加

### Phase 2 (新增)
LightRAG 响应中包含三类数据源，但当前只使用了 chunks：
1. **chunks**: 文档片段（有 `file_path` = entryId）✅ 已使用
2. **entities**: 知识图谱实体（有 `file_path` = entryId）❌ 未使用
3. **relationships**: 知识图谱关系（有 `file_path` = entryId）❌ 未使用

此外，`only_need_context=False` 时 chunks 不返回 score，需要改用 LLM 推荐度。

## What Changes

### Phase 1 (已完成)
- **MODIFIED**: `recommend_entry_relations` 方法改为单阶段流程
- 使用 `_build_entry_relation_recommendation_prompt` 构建结构化输出 prompt
- 保留严格的 JSON 解析和 ID 校验兜底逻辑

### Phase 2 (新增)
- **MODIFIED**: `_call_rag_relation_recommend_sync` 返回完整图谱上下文
- **MODIFIED**: 候选 entryId 来源扩展为 chunks + entities + relationships
- **MODIFIED**: 用 LLM 输出的 `relevance` 字段替代 chunk score
- **MODIFIED**: prompt 增加推荐度判断维度指令

## Impact

- Affected specs: `lightrag-relation`
- Affected code:
  - `backend/app/lightrag/service.py`: `_call_rag_relation_recommend_sync`, `recommend_entry_relations`, prompt 构建函数
  - `backend/app/lightrag/schemas.py`: `LightRagEntryRelationRecommendationItem` 字段调整
  - `backend/tests/test_lightrag_relation_recommendations.py`: 测试用例更新

## Risks & Mitigations

| 风险 | 缓解措施 |
|------|----------|
| LLM 幻觉生成不存在的 ID | 白名单校验：targetEntryId 必须在 chunks/entities/relationships 的 file_path 中 |
| LLM 推荐度不准确 | 提供明确的判断维度指令（语义相关性、知识图谱关联强度等） |
| JSON 解析失败 | 保留多种解析策略 + 降级返回候选列表（relevance=0.5） |
| entities/relationships 数据量大 | 按 file_path 去重，限制候选数量 |
